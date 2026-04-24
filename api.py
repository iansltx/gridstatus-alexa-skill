import logging
from datetime import datetime, timedelta
from datetime import timezone as tz_module
from typing import Dict


class EIADataDelayError(Exception):
    """Raised when an EIA BA query returns no data for a recent time window.

    EIA balancing-authority data is typically posted on a one-to-two day
    delay.  When the requested time is within roughly three days of *now*
    and the API returns nothing, this exception signals that the absence of
    data is most likely due to that posting lag rather than a genuine data
    gap.
    """

    pass


try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:
    from backports.zoneinfo import (  # type: ignore[no-redef]
        ZoneInfo,
        ZoneInfoNotFoundError,
    )

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timezone mapping for ISO / EIA-BA identifiers
# ---------------------------------------------------------------------------
# Each entry maps an ISO or Balancing Authority code to the IANA timezone that
# best represents its primary service territory.  When a user says "3 PM" for a
# given grid, the skill interprets that wall-clock time in this timezone and
# converts it to UTC before querying the API.
#
# Notes on ambiguous multi-timezone operators:
#   MISO  – spans Central/Eastern; Central used (majority of load)
#   TVA   – spans Central/Eastern; Central used (most of AL/MS/TN-west)
#   SOCO  – spans Central/Eastern; Central used (AL/MS primary territory)
#   IESO  – Ontario, Canada → America/Toronto (Eastern)
#   IPCO  – Idaho Power → America/Boise (Mountain, observes DST unlike Phoenix)
#   WALC  – Western Area Power Desert Southwest → America/Phoenix (no DST)
#   DEAA  – Arlington Valley (AZ) → America/Phoenix (no DST)
#   SRP   – Salt River Project (AZ) → America/Phoenix (no DST)
#   TEPC  – Tucson Electric Power (AZ) → America/Phoenix (no DST)
#   AZPS  – Arizona Public Service → America/Phoenix (no DST)
ISO_TIMEZONES: Dict[str, str] = {
    # ---- Major ISO/RTOs ----
    "ERCOT": "America/Chicago",
    "CAISO": "America/Los_Angeles",
    "ISONE": "America/New_York",
    "NYISO": "America/New_York",
    "MISO": "America/Chicago",
    "PJM": "America/New_York",
    "SPP": "America/Chicago",
    "IESO": "America/Toronto",
    # ---- EIA Balancing Authorities ----
    "AECI": "America/Chicago",  # Associated Electric Cooperation, MO
    "AVA": "America/Los_Angeles",  # Avista, Pacific NW (WA/ID/OR)
    "AVRN": "America/New_York",  # Avangrid Renewables, primarily New England
    "AZPS": "America/Phoenix",  # Arizona Public Service (no DST)
    "BANC": "America/Los_Angeles",  # Balancing Authority of Northern California
    "BPAT": "America/Los_Angeles",  # Bonneville Power Administration, Pacific NW
    "CHPD": "America/Los_Angeles",  # Chelan County PUD, WA
    "CPLE": "America/New_York",  # Duke Energy Progress East, NC
    "CPLW": "America/New_York",  # Duke Energy Progress West, NC
    "DEAA": "America/Phoenix",  # Arlington Valley, AZ (no DST)
    "DOPD": "America/Los_Angeles",  # Douglas County PUD, WA
    "DUK": "America/New_York",  # Duke Energy Carolinas, NC/SC
    "EPE": "America/Denver",  # El Paso Electric, NM/TX
    "FMPP": "America/New_York",  # Florida Municipal Power Pool
    "FPC": "America/New_York",  # Duke Energy Florida
    "FPL": "America/New_York",  # Florida Power and Light
    "GCPD": "America/Los_Angeles",  # Grant County PUD, WA
    "GRID": "America/Los_Angeles",  # Gridforce Energy Management, Pacific NW
    "GVL": "America/New_York",  # Gainesville Regional Utilities, FL
    "GWA": "America/Denver",  # NaturEner Power Watch, MT
    "HST": "America/New_York",  # City of Homestead, FL
    "IID": "America/Los_Angeles",  # Imperial Irrigation District, CA
    "IPCO": "America/Boise",  # Idaho Power (Mountain, observes DST)
    "JEA": "America/New_York",  # JEA, Jacksonville, FL
    "LDWP": "America/Los_Angeles",  # LA Department of Water and Power
    "LGEE": "America/New_York",  # LG&E, Louisville, KY (Eastern)
    "NEVP": "America/Los_Angeles",  # Nevada Power
    "NWMT": "America/Denver",  # NorthWestern Corporation, MT
    "PACE": "America/Denver",  # PacifiCorp East, UT/WY
    "PACW": "America/Los_Angeles",  # PacifiCorp West, OR/WA
    "PGE": "America/Los_Angeles",  # Portland General Electric, OR
    "PNM": "America/Denver",  # Public Service New Mexico
    "PSCO": "America/Denver",  # Public Service Colorado
    "PSEI": "America/Los_Angeles",  # Puget Sound Energy, WA
    "SC": "America/New_York",  # South Carolina Public Service Authority
    "SCEG": "America/New_York",  # Dominion Energy South Carolina
    "SCL": "America/Los_Angeles",  # Seattle City Light
    "SEC": "America/New_York",  # Seminole Electric Cooperative, FL
    "SEPA": "America/New_York",  # Southeastern Power Administration
    "SIKE": "America/Chicago",  # Sikeston Board of Municipal Utilities, MO
    "SOCO": "America/Chicago",  # Southern Company (AL/MS primary territory)
    "SPA": "America/Chicago",  # Southwestern Power Administration
    "SRP": "America/Phoenix",  # Salt River Project, AZ (no DST)
    "SWPP": "America/Chicago",  # Southwest Power Pool (EIA code for SPP)
    "TAL": "America/New_York",  # City of Tallahassee, FL
    "TEC": "America/New_York",  # Tampa Electric, FL
    "TEPC": "America/Phoenix",  # Tucson Electric Power, AZ (no DST)
    "TIDC": "America/Los_Angeles",  # Turlock Irrigation District, CA
    "TPWR": "America/Los_Angeles",  # Tacoma Power, WA
    "TVA": "America/Chicago",  # Tennessee Valley Authority (Central)
    "WACM": "America/Denver",  # Western Area Power Admin – Rockies
    "WALC": "America/Phoenix",  # Western Area Power Admin – Desert SW (no DST)
    "WAUW": "America/Denver",  # Western Area Power Admin – Upper Great Plains W
    "WWA": "America/Denver",  # NaturEner Wind Watch, MT
    "YAD": "America/New_York",  # Alcoa (Yadkin), NC
}


# ---------------------------------------------------------------------------
# Friendly timezone names for speech output
# ---------------------------------------------------------------------------
# Maps IANA timezone IDs to human-friendly names suitable for Alexa speech.
# America/Phoenix is "Arizona Time" because Arizona (outside Navajo Nation)
# does not observe DST, making it distinctively different from Mountain Time.
FRIENDLY_TZ_NAMES: Dict[str, str] = {
    "America/New_York": "Eastern Time",
    "America/Toronto": "Eastern Time",
    "America/Chicago": "Central Time",
    "America/Denver": "Mountain Time",
    "America/Boise": "Mountain Time",
    "America/Phoenix": "Arizona Time",
    "America/Los_Angeles": "Pacific Time",
    "UTC": "UTC",
}


def _friendly_tz_name(iana_tz: str) -> str:
    """Return a speech-friendly timezone label for an IANA timezone string.

    If the IANA ID is not in ``FRIENDLY_TZ_NAMES``, derives a name by splitting
    on ``/``, taking the second component, replacing underscores with spaces,
    and appending `` Time``.  For example, ``America/New_York`` → ``New York Time``
    and ``America/Los_Angeles`` → ``Los Angeles Time``.
    """
    if iana_tz in FRIENDLY_TZ_NAMES:
        return FRIENDLY_TZ_NAMES[iana_tz]
    parts = iana_tz.split("/")
    if len(parts) >= 2:
        return parts[1].replace("_", " ") + " Time"
    return iana_tz + " Time"


def _ordinal_suffix(day: int) -> str:
    """Return the ordinal suffix for a day-of-month integer (1→'st', etc.)."""
    if 11 <= day <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def _format_local_time_for_speech(dt: datetime, iso_tz) -> str:
    """
    Convert a UTC-aware datetime to a speech-friendly string in *iso_tz*.

    Returns a string like ``"3:45 PM Central Time on January 15th"``.

    Args:
        dt: A timezone-aware datetime (typically UTC).
        iso_tz: A :class:`~zoneinfo.ZoneInfo` instance for the target timezone.

    Returns:
        A formatted string suitable for Alexa speech.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz_module.utc)
    local = dt.astimezone(iso_tz)

    hour = local.hour % 12 or 12
    minute = local.minute
    am_pm = "AM" if local.hour < 12 else "PM"

    if minute == 0:
        time_part = f"{hour} {am_pm}"
    else:
        time_part = f"{hour}:{minute:02d} {am_pm}"

    tz_label = _friendly_tz_name(getattr(iso_tz, "key", "UTC"))

    day = local.day
    month_name = local.strftime("%B")
    date_part = f"{month_name} {day}{_ordinal_suffix(day)}"

    return f"{time_part} {tz_label} on {date_part}"


def get_iso_timezone(iso: str) -> ZoneInfo:
    """
    Return a :class:`~zoneinfo.ZoneInfo` for the given ISO or EIA-BA code.

    Falls back to UTC for any unrecognized code so the skill degrades
    gracefully rather than raising an exception.

    Args:
        iso: ISO or BA code string (case-insensitive), e.g. ``"ERCOT"``.

    Returns:
        A :class:`~zoneinfo.ZoneInfo` instance for the operator's primary
        local timezone.
    """
    tz_name = ISO_TIMEZONES.get(iso.upper(), "UTC")
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.warning(
            "Timezone '%s' not found for ISO '%s'; falling back to UTC",
            tz_name,
            iso,
        )
        return ZoneInfo("UTC")


# Mapping from ISO codes to GridStatus dataset names
ISO_FUEL_MIX_DATASETS = {
    "ERCOT": "ercot_fuel_mix",
    "CAISO": "caiso_fuel_mix",
    "ISONE": "isone_fuel_mix",
    "NYISO": "nyiso_fuel_mix",
    "MISO": "miso_fuel_mix",
    "PJM": "pjm_fuel_mix",
    "SPP": "spp_fuel_mix",
    "IESO": "ieso_fuel_mix",
}

# EIA Balancing Authority codes
EIA_BA_CODES = {
    "AECI",
    "AVA",
    "AVRN",
    "AZPS",
    "BANC",
    "BPAT",
    "CHPD",
    "CPLE",
    "CPLW",
    "DEAA",
    "DOPD",
    "DUK",
    "EPE",
    "FMPP",
    "FPC",
    "FPL",
    "GCPD",
    "GRID",
    "GVL",
    "GWA",
    "HST",
    "IID",
    "IPCO",
    "JEA",
    "LDWP",
    "LGEE",
    "NEVP",
    "NWMT",
    "PACE",
    "PACW",
    "PGE",
    "PNM",
    "PSCO",
    "PSEI",
    "SC",
    "SCEG",
    "SCL",
    "SEC",
    "SEPA",
    "SIKE",
    "SOCO",
    "SPA",
    "SRP",
    "SWPP",
    "TAL",
    "TEC",
    "TEPC",
    "TIDC",
    "TPWR",
    "TVA",
    "WACM",
    "WALC",
    "WAUW",
    "WWA",
    "YAD",
}

# Metadata columns to exclude when building fuel mix
NON_FUEL_COLUMNS = frozenset(
    {
        "interval_start_utc",
        "interval_end_utc",
        "interval_start_local",
        "interval_end_local",
    }
)

# Human-readable display names for fuel type columns
FUEL_DISPLAY_NAMES = {
    "coal": "coal",
    "coal_and_lignite": "coal and lignite",
    "natural_gas": "natural gas",
    "gas": "natural gas",
    "nuclear": "nuclear",
    "wind": "wind",
    "solar": "solar",
    "hydro": "hydro",
    "large_hydro": "large hydro",
    "small_hydro": "small hydro",
    "geothermal": "geothermal",
    "biomass": "biomass",
    "biogas": "biogas",
    "other": "other",
    "other_fossil_fuels": "other fossil fuels",
    "other_renewables": "other renewables",
    "batteries": "batteries",
    "storage": "storage",
    "power_storage": "storage",
    "imports": "imports",
    "biofuel": "biofuel",
    "oil": "oil",
    "dual_fuel": "dual fuel",
    "multiple_fuels": "multiple fuels",
    "cogen": "cogeneration",
    "waste_disposal": "waste",
    "diesel_fuel_oil": "diesel",
    "pumped_storage": "pumped storage",
    "battery_storage": "battery storage",
    "petroleum": "petroleum",
    "solar_with_integrated_battery_storage": "solar with battery storage",
    "wind_with_integrated_battery_storage": "wind with battery storage",
    "other_energy_storage": "other energy storage",
    "unknown_energy_storage": "energy storage",
}

# Metadata columns in eia_fuel_mix_hourly that are not fuel types.
_EIA_META_COLS: frozenset = frozenset({"respondent", "respondent_name"})


def _to_utc_naive(dt):
    """Convert a datetime to UTC-naive for comparison purposes."""
    if dt is None:
        return None
    if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
        return dt.astimezone(tz_module.utc).replace(tzinfo=None)
    return dt


def _query_fuel_mix_dataset(client, iso, dataset, target_time):
    """
    Query a GridStatus fuel mix dataset for a given ISO and target time.

    Args:
        client: GridStatus API client
        iso: ISO code string (e.g. "ERCOT")
        dataset: Dataset name string (e.g. "ercot_fuel_mix")
        target_time: datetime object representing the desired time

    Returns:
        dict with keys: iso, time, fuel_mix

    Raises:
        ValueError: If no data is found
    """
    start = target_time - timedelta(hours=1)
    end = target_time + timedelta(hours=1)

    start_str = start.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    end_str = end.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    logger.info(
        "Querying dataset %s for ISO %s between %s and %s",
        dataset,
        iso,
        start_str,
        end_str,
    )

    data = client.get_dataset(
        dataset=dataset,
        start=start_str,
        end=end_str,
        limit=20,
        return_format="python",
        verbose=False,
    )

    if not data:
        raise ValueError(f"No data returned from dataset '{dataset}' for ISO '{iso}'")

    target_naive = _to_utc_naive(target_time)

    def record_distance(record):
        interval_start = record.get("interval_start_utc")
        if interval_start is None:
            return float("inf")
        interval_naive = _to_utc_naive(interval_start)
        if interval_naive is None or target_naive is None:
            return float("inf")
        return abs((interval_naive - target_naive).total_seconds())

    closest = min(data, key=record_distance)

    fuel_mix = {}
    for key, value in closest.items():
        if key in NON_FUEL_COLUMNS:
            continue
        if isinstance(value, (int, float)):
            fuel_mix[key] = value

    record_time = closest.get("interval_start_utc")

    logger.info("Found %d fuel types for ISO %s at %s", len(fuel_mix), iso, record_time)

    return {
        "iso": iso,
        "time": record_time,
        "fuel_mix": fuel_mix,
    }


def _query_eia_ba_fuel_mix(client, ba_code, target_time):
    """
    Query the EIA fuel mix hourly dataset for a specific Balancing Authority.

    eia_fuel_mix_hourly is wide-format: one row per (respondent, hour) with
    each fuel type as its own numeric column.  The former eia_grid_monitor
    dataset (long-format, one row per fuel type) has been retired.

    Args:
        client: GridStatus API client
        ba_code: EIA Balancing Authority code string (e.g. "TVA")
        target_time: datetime object representing the desired time

    Returns:
        dict with keys: iso, time, fuel_mix

    Raises:
        ValueError: If no data is found
    """
    start = target_time - timedelta(hours=2)
    end = target_time + timedelta(hours=2)

    start_str = start.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    end_str = end.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    logger.info(
        "Querying eia_fuel_mix_hourly for BA %s between %s and %s",
        ba_code,
        start_str,
        end_str,
    )

    data = client.get_dataset(
        dataset="eia_fuel_mix_hourly",
        start=start_str,
        end=end_str,
        filter_column="respondent",
        filter_value=ba_code,
        limit=20,
        return_format="python",
        verbose=False,
    )

    if not data:
        # Determine whether the empty result is likely caused by the EIA
        # posting delay (~1–2 days for most BAs) rather than a genuine
        # data gap.  Use a 3-day (72-hour) window to be conservative.
        now_naive = datetime.utcnow()
        target_naive = _to_utc_naive(target_time)
        if target_naive is not None:
            age_hours = (now_naive - target_naive).total_seconds() / 3600
        else:
            age_hours = float("inf")

        if age_hours < 72:
            raise EIADataDelayError(ba_code)

        raise ValueError(
            f"No data returned from eia_fuel_mix_hourly for Balancing Authority '{ba_code}'"
        )

    target_naive = _to_utc_naive(target_time)

    def record_distance(record):
        interval_start = record.get("interval_start_utc")
        if interval_start is None:
            return float("inf")
        interval_naive = _to_utc_naive(interval_start)
        if interval_naive is None or target_naive is None:
            return float("inf")
        return abs((interval_naive - target_naive).total_seconds())

    closest = min(data, key=record_distance)
    record_time = closest.get("interval_start_utc")

    fuel_mix = {}
    for key, value in closest.items():
        if key in NON_FUEL_COLUMNS:
            continue
        if key in _EIA_META_COLS:
            continue
        if isinstance(value, (int, float)):
            fuel_mix[key] = value

    if not fuel_mix:
        raise ValueError(
            f"No fuel mix data could be extracted for Balancing Authority '{ba_code}'"
        )

    logger.info(
        "Found %d fuel types for BA %s at %s", len(fuel_mix), ba_code, record_time
    )

    return {
        "iso": ba_code,
        "time": record_time,
        "fuel_mix": fuel_mix,
    }


def get_fuel_mix(client, iso, target_time):
    """
    Retrieve the fuel mix for a given ISO or Balancing Authority at a target time.

    Args:
        client: GridStatus API client
        iso: ISO or BA code string (case-insensitive)
        target_time: datetime object representing the desired time

    Returns:
        dict with keys: iso, time, fuel_mix

    Raises:
        ValueError: If the ISO/BA is unsupported or no data is found
    """
    iso_upper = iso.upper()

    if iso_upper in ISO_FUEL_MIX_DATASETS:
        dataset = ISO_FUEL_MIX_DATASETS[iso_upper]
        logger.info("Dispatching %s to dataset query: %s", iso_upper, dataset)
        return _query_fuel_mix_dataset(client, iso_upper, dataset, target_time)

    if iso_upper in EIA_BA_CODES:
        logger.info("Dispatching %s to EIA BA query", iso_upper)
        return _query_eia_ba_fuel_mix(client, iso_upper, target_time)

    raise ValueError(
        f"Unsupported ISO or Balancing Authority: '{iso}'. "
        f"Supported ISOs: {sorted(ISO_FUEL_MIX_DATASETS.keys())}. "
        f"Supported EIA BA codes: {sorted(EIA_BA_CODES)}."
    )


def format_fuel_mix_speech(result, iso_display_name, iso_tz=None, is_current=True):
    """
    Format a fuel mix result dict into a human-readable Alexa speech string.

    Args:
        result: dict with keys 'fuel_mix' (dict mapping fuel type to MW) and
            'time' (a UTC-aware datetime for the data interval).
        iso_display_name: Human-readable name for the ISO/BA (e.g. "ERCOT").
        iso_tz: Optional :class:`~zoneinfo.ZoneInfo` for the grid operator's
            local timezone.  Used to display the data timestamp in local time
            when *is_current* is ``False``.
        is_current: When ``True`` (default) the response says "right now".
            When ``False`` the actual data date, time, and timezone are
            included in the response.

    Returns:
        A speech string suitable for Alexa to read aloud.
    """
    fuel_mix = result.get("fuel_mix", {})

    if not fuel_mix:
        return f"I'm sorry, I couldn't find any fuel mix data for {iso_display_name}."

    positive = {
        k: v for k, v in fuel_mix.items() if isinstance(v, (int, float)) and v > 0
    }

    if not positive:
        return f"The energy mix for {iso_display_name} currently shows no positive generation values."

    total = sum(positive.values())

    if total <= 0:
        return f"The energy mix for {iso_display_name} currently shows no positive generation values."

    sorted_fuels = sorted(positive.items(), key=lambda x: x[1], reverse=True)

    parts = []
    for fuel_key, mw in sorted_fuels:
        pct = round(mw / total * 100)
        if pct < 1:
            continue
        display = FUEL_DISPLAY_NAMES.get(fuel_key, fuel_key.replace("_", " "))
        parts.append(f"{pct}% {display}")

    if not parts:
        return (
            f"The energy mix for {iso_display_name} is available, "
            f"but all sources account for less than 1% individually."
        )

    if len(parts) == 1:
        mix_str = parts[0]
    elif len(parts) == 2:
        mix_str = f"{parts[0]} and {parts[1]}"
    else:
        mix_str = ", ".join(parts[:-1]) + f", and {parts[-1]}"

    # Build the time context phrase and choose the appropriate verb tense.
    if is_current:
        time_context = "right now"
        verb = "is"
    else:
        record_time = result.get("time")
        if record_time is not None and iso_tz is not None:
            time_label = _format_local_time_for_speech(record_time, iso_tz)
            time_context = f"at {time_label}"
        else:
            time_context = None
        verb = "was" if time_context else "is"

    if time_context:
        return f"The energy mix for {iso_display_name} {time_context} {verb} {mix_str}."
    else:
        return f"The energy mix for {iso_display_name} {verb} {mix_str}."
