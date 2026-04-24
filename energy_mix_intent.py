"""Shared intent-handling logic for the CurrentEnergyMix intent.

Used by both the Alexa skill (``lambda_function.py``) and the Dialogflow
webhook (``main.py``) so that changes to the core business logic only need
to be made in one place.

The two callers differ only in *how* they extract the raw slot / parameter
values from their respective platform request objects; once those three
strings (``iso``, ``time_str``, ``date_str``) are in hand they both delegate
to :func:`handle_current_energy_mix`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

import api
from gridstatus_lite import GridStatusClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ISO / Balancing-Authority display names
# ---------------------------------------------------------------------------
# Human-readable names used in spoken responses.  Centralised here so both
# the Alexa skill and the Dialogflow webhook use identical phrasing.

ISO_DISPLAY_NAMES: dict[str, str] = {
    "ERCOT": "ERCOT",
    "CAISO": "CAISO",
    "ISONE": "ISO New England",
    "NYISO": "the New York ISO",
    "MISO": "MISO",
    "PJM": "PJM",
    "SPP": "SPP",
    "IESO": "the Ontario grid",
    "AECI": "AECI",
    "AVA": "Avista",
    "AVRN": "Avangrid",
    "AZPS": "Arizona Public Service",
    "BANC": "the Balancing Authority of Northern California",
    "BPAT": "Bonneville Power Administration",
    "CHPD": "Chelan County PUD",
    "CPLE": "Duke Energy Progress East",
    "CPLW": "Duke Energy Progress West",
    "DEAA": "Arlington Valley",
    "DOPD": "Douglas County PUD",
    "DUK": "Duke Energy Carolinas",
    "EPE": "El Paso Electric",
    "FMPP": "the Florida Municipal Power Pool",
    "FPC": "Duke Energy Florida",
    "FPL": "Florida Power and Light",
    "GCPD": "Grant County PUD",
    "GRID": "Gridforce Energy Management",
    "GVL": "Gainesville Regional Utilities",
    "GWA": "NaturEner Power Watch",
    "HST": "the City of Homestead",
    "IID": "Imperial Irrigation District",
    "IPCO": "Idaho Power",
    "JEA": "JEA",
    "LDWP": "the LA Department of Water and Power",
    "LGEE": "LG&E",
    "NEVP": "Nevada Power",
    "NWMT": "NorthWestern Corporation",
    "PACE": "PacifiCorp East",
    "PACW": "PacifiCorp West",
    "PGE": "Portland General Electric",
    "PNM": "Public Service New Mexico",
    "PSCO": "Public Service Colorado",
    "PSEI": "Puget Sound Energy",
    "SC": "the South Carolina Public Service Authority",
    "SCEG": "Dominion Energy South Carolina",
    "SCL": "Seattle City Light",
    "SEC": "Seminole Electric Cooperative",
    "SEPA": "the Southeastern Power Administration",
    "SIKE": "Sikeston Board of Municipal Utilities",
    "SOCO": "Southern Company",
    "SPA": "the Southwestern Power Administration",
    "SRP": "Salt River Project",
    "SWPP": "Southwest Power Pool",
    "TAL": "Tallahassee",
    "TEC": "Tampa Electric",
    "TEPC": "Tucson Electric Power",
    "TIDC": "Turlock Irrigation District",
    "TPWR": "Tacoma Power",
    "TVA": "the Tennessee Valley Authority",
    "WACM": "the Western Area Power Administration Rockies",
    "WALC": "the Western Area Power Administration Desert Southwest",
    "WAUW": "the Western Area Power Administration Upper Great Plains West",
    "WWA": "NaturEner Wind Watch",
    "YAD": "Alcoa",
}


# ---------------------------------------------------------------------------
# Core handler
# ---------------------------------------------------------------------------


def handle_current_energy_mix(
    client: GridStatusClient,
    iso: Optional[str],
    time_str: Optional[str],
    date_str: Optional[str],
) -> Tuple[str, Optional[str]]:
    """Core handler for the CurrentEnergyMix intent.

    Validates inputs, fetches the fuel mix via the GridStatus API, and
    returns a speech-ready response string together with an optional reprompt.

    Parameters
    ----------
    client:
        An initialised :class:`~gridstatus_lite.GridStatusClient`.
    iso:
        Canonical ISO / Balancing-Authority code (e.g. ``"ERCOT"``).
        Should already be resolved from the platform's entity system.
        Pass ``None`` if the slot / parameter was not filled.
    time_str:
        24-hour time in ``"HH:MM"`` format (matches the Alexa ``AMAZON.TIME``
        slot format), or ``None`` if no time was provided.
        Callers that receive Dialogflow ``@sys.time`` values (ISO-8601
        timestamps) must strip them down to ``"HH:MM"`` before calling this
        function — see :func:`main._extract_time_str`.
    date_str:
        Calendar date in ``"YYYY-MM-DD"`` format, or ``None``.
        Callers that receive Dialogflow ``@sys.date`` values (ISO-8601
        timestamps) must strip them down to ``"YYYY-MM-DD"`` before calling
        this function — see :func:`main._extract_date_str`.

    Returns
    -------
    speech : str
        The response text to speak or return.
    reprompt : str | None
        Reprompt / follow-up text when more information is needed
        (multi-turn dialog).  ``None`` signals that the session / turn is
        complete and no further prompt is required.
    """

    # --- Validate ISO -------------------------------------------------------
    if not iso:
        speech = (
            "Which electric grid or power system would you like information for? "
            "For example, say ERCOT, CAISO, or PJM."
        )
        reprompt = "Please tell me which grid system you want information for."
        return speech, reprompt

    iso = iso.upper()
    now = datetime.now(timezone.utc)
    iso_tz = api.get_iso_timezone(iso)

    # --- Date supplied but time omitted → ask for time ----------------------
    if date_str and not time_str:
        speech = (
            "What time would you like the fuel mix for? For example, say 3 PM or noon."
        )
        reprompt = "Please provide a time, for example, 3 PM."
        return speech, reprompt

    # --- Parse target datetime (default: now) -------------------------------
    target_time = now

    if time_str:
        try:
            parts = time_str.split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0

            if date_str and len(date_str) == 10:  # explicit date: "YYYY-MM-DD"
                target_date = datetime.strptime(date_str, "%Y-%m-%d")
                # Construct in the ISO's local timezone so DST is handled
                # correctly, then convert to UTC for the API query.
                target_time = datetime(
                    target_date.year,
                    target_date.month,
                    target_date.day,
                    hour,
                    minute,
                    tzinfo=iso_tz,
                ).astimezone(timezone.utc)
            else:
                # Time only: interpret the wall-clock hour in the ISO's local
                # timezone (e.g. "3 PM" means 3 PM Central for ERCOT).
                now_local = now.astimezone(iso_tz)
                target_time = now_local.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                ).astimezone(timezone.utc)

        except (ValueError, IndexError):
            speech = (
                "I didn't understand that time. "
                "Please try again, for example, say 3 PM."
            )
            reprompt = "What time would you like the fuel mix for?"
            return speech, reprompt

    # --- Reject future times ------------------------------------------------
    if target_time > now:
        speech = (
            "I can only provide data for times that have already passed. "
            "Please ask about a time in the past."
        )
        reprompt = "What time in the past would you like the fuel mix for?"
        return speech, reprompt

    # --- Fetch and format ---------------------------------------------------
    iso_display = ISO_DISPLAY_NAMES.get(iso, iso)
    is_current = time_str is None and date_str is None

    try:
        result = api.get_fuel_mix(client, iso, target_time)
        speech = api.format_fuel_mix_speech(
            result, iso_display, iso_tz=iso_tz, is_current=is_current
        )
    except ValueError as e:
        logger.error("Fuel mix not available for %s: %s", iso, e)
        speech = f"I'm sorry, I couldn't find fuel mix data for {iso_display}. {e}"
    except Exception as e:
        logger.error(
            "Unexpected error fetching fuel mix for %s: %s", iso, e, exc_info=True
        )
        speech = (
            f"I'm sorry, there was an error getting the energy mix for "
            f"{iso_display}. Please try again later."
        )

    # reprompt=None → caller should end the session / turn
    return speech, None
