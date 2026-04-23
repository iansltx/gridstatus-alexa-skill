# -*- coding: utf-8 -*-

import logging
import os
from datetime import datetime, timezone

import boto3
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_core.skill_builder import CustomSkillBuilder
from ask_sdk_core.utils import is_intent_name, is_request_type
from ask_sdk_dynamodb.adapter import DynamoDbAdapter
from ask_sdk_model import Response
from gridstatusio import GridStatusClient

import api

SKILL_NAME = "Grid Status"

ISO_DISPLAY_NAMES = {
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

ddb_region = os.environ.get('DYNAMODB_PERSISTENCE_REGION')
ddb_table_name = os.environ.get('DYNAMODB_PERSISTENCE_TABLE_NAME')
ddb_resource = boto3.resource('dynamodb', region_name=ddb_region)
dynamodb_adapter = DynamoDbAdapter(table_name=ddb_table_name, create_table=False, dynamodb_resource=ddb_resource)
sb = CustomSkillBuilder(persistence_adapter=dynamodb_adapter)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _load_config() -> dict:
    table = ddb_resource.Table(ddb_table_name)
    response = table.get_item(Key={"id": "config"})
    return response.get("Item", {}).get("attributes", {})


config = _load_config()
GRID_STATUS_API_KEY = config.get("api_key")
grid_status_client = GridStatusClient(api_key=GRID_STATUS_API_KEY)


@sb.request_handler(can_handle_func=is_request_type("LaunchRequest"))
def launch_request_handler(handler_input):
    """Handler for Skill Launch."""
    # type: (HandlerInput) -> Response
    attr = handler_input.attributes_manager.persistent_attributes
    if not attr:
        attr["locality"] = ""

    handler_input.attributes_manager.session_attributes = attr

    speech_text = "Welcome to Grid Status. Let me know what data you want me to access."
    reprompt = "Let me know what data you want me to access."

    handler_input.response_builder.speak(speech_text).ask(reprompt)
    return handler_input.response_builder.response


@sb.request_handler(can_handle_func=is_intent_name("AMAZON.HelpIntent"))
def help_intent_handler(handler_input):
    """Handler for Help Intent."""
    # type: (HandlerInput) -> Response
    speech_text = "I can access data from Grid Status dot IO."
    reprompt = (
        "For example, you can ask me what the generation mix is in Texas right now."
    )

    handler_input.response_builder.speak(speech_text).ask(reprompt)
    return handler_input.response_builder.response


@sb.request_handler(
    can_handle_func=lambda input: (
        is_intent_name("AMAZON.CancelIntent")(input)
        or is_intent_name("AMAZON.StopIntent")(input)
    )
)
def cancel_and_stop_intent_handler(handler_input):
    """Single handler for Cancel and Stop Intent."""
    # type: (HandlerInput) -> Response
    speech_text = "Exiting."

    handler_input.response_builder.speak(speech_text).set_should_end_session(True)
    return handler_input.response_builder.response


@sb.request_handler(can_handle_func=is_request_type("SessionEndedRequest"))
def session_ended_request_handler(handler_input):
    """Handler for Session End."""
    # type: (HandlerInput) -> Response
    logger.info(
        "Session ended with reason: {}".format(
            handler_input.request_envelope.request.reason
        )
    )
    return handler_input.response_builder.response


def locality(handler_input):
    """Future: store locality so the end user can ask for information based on where they are."""
    # type: (HandlerInput) -> string
    session_attr = handler_input.attributes_manager.session_attributes

    if "locality" in session_attr:
        return session_attr.locality

    return ""


@sb.request_handler(
    can_handle_func=lambda input: is_intent_name("CurrentEnergyMix")(input)
)
def current_energy_mix_handler(handler_input):
    """Handler for processing energy mix request."""
    # type: (HandlerInput) -> Response
    slots = handler_input.request_envelope.request.intent.slots

    # --- Extract slot values ---
    iso_slot = slots.get("iso")
    time_slot = slots.get("time")
    date_slot = slots.get("date")

    # Resolve ISO: prefer canonical resolved entity value over raw spoken value
    # (so synonyms like "Texas" correctly resolve to "ERCOT")
    iso = None
    if iso_slot:
        if iso_slot.resolutions and iso_slot.resolutions.resolutions_per_authority:
            for res in iso_slot.resolutions.resolutions_per_authority:
                if (
                    res.status
                    and hasattr(res.status.code, "value")
                    and res.status.code.value == "ER_SUCCESS_MATCH"
                    and res.values
                ):
                    iso = res.values[0].value.name
                    break
        if not iso:
            iso = iso_slot.value

    time_str = time_slot.value if (time_slot and time_slot.value) else None
    date_str = date_slot.value if (date_slot and date_slot.value) else None

    # --- Validate ISO ---
    if not iso:
        speech = (
            "Which electric grid or power system would you like information for? "
            "For example, say ERCOT, CAISO, or PJM."
        )
        reprompt = "Please tell me which grid system you want information for."
        handler_input.response_builder.speak(speech).ask(reprompt)
        return handler_input.response_builder.response

    iso = iso.upper()
    now = datetime.now(timezone.utc)

    # --- Date/time slot logic ---
    # Rule: if date is provided but time is not → reprompt for time
    if date_str and not time_str:
        speech = (
            "What time would you like the fuel mix for? For example, say 3 PM or noon."
        )
        reprompt = "Please provide a time, for example, 3 PM."
        handler_input.response_builder.speak(speech).ask(reprompt)
        return handler_input.response_builder.response

    # Parse the target datetime (default: current time)
    target_time = now

    if time_str:
        try:
            # Alexa TIME slot: 24-hour "HH:MM" format
            time_parts = time_str.split(":")
            hour = int(time_parts[0])
            minute = int(time_parts[1]) if len(time_parts) > 1 else 0

            if date_str and len(date_str) == 10:  # Specific date: "YYYY-MM-DD"
                target_date = datetime.strptime(date_str, "%Y-%m-%d")
                target_time = datetime(
                    target_date.year,
                    target_date.month,
                    target_date.day,
                    hour,
                    minute,
                    tzinfo=timezone.utc,
                )
            else:
                # Time only (no date) → assume today in UTC
                target_time = now.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )
        except (ValueError, IndexError):
            speech = (
                "I didn't understand that time. "
                "Please try again, for example, say 3 PM."
            )
            reprompt = "What time would you like the fuel mix for?"
            handler_input.response_builder.speak(speech).ask(reprompt)
            return handler_input.response_builder.response

    # --- Validate: reprompt if target time is in the future ---
    if target_time > now:
        speech = (
            "I can only provide data for times that have already passed. "
            "Please ask about a time in the past."
        )
        reprompt = "What time in the past would you like the fuel mix for?"
        handler_input.response_builder.speak(speech).ask(reprompt)
        return handler_input.response_builder.response

    # --- Fetch and format fuel mix ---
    iso_display = ISO_DISPLAY_NAMES.get(iso, iso)

    try:
        result = api.get_fuel_mix(grid_status_client, iso, target_time)
        speech_text = api.format_fuel_mix_speech(result, iso_display)
    except ValueError as e:
        logger.error("Fuel mix not available for %s: %s", iso, e)
        speech_text = (
            f"I'm sorry, I couldn't find fuel mix data for {iso_display}. {str(e)}"
        )
    except Exception as e:
        logger.error(
            "Unexpected error fetching fuel mix for %s: %s", iso, e, exc_info=True
        )
        speech_text = (
            f"I'm sorry, there was an error getting the energy mix for "
            f"{iso_display}. Please try again later."
        )

    handler_input.response_builder.speak(speech_text)
    return handler_input.response_builder.response


@sb.request_handler(
    can_handle_func=lambda input: (
        is_intent_name("AMAZON.FallbackIntent")(input)
        or is_intent_name("AMAZON.YesIntent")(input)
        or is_intent_name("AMAZON.NoIntent")(input)
    )
)
def fallback_handler(handler_input):
    """AMAZON.FallbackIntent is only available in en-US locale.
    This handler will not be triggered except in that locale,
    so it is safe to deploy on any locale.
    """
    # type: (HandlerInput) -> Response
    session_attr = handler_input.attributes_manager.session_attributes

    speech_text = (
        "The {} skill can't help you with that.  "
        "Try asking something about the grid.".format(SKILL_NAME)
    )
    reprompt = "Try asking about something electricity grid data related."

    handler_input.response_builder.speak(speech_text).ask(reprompt)
    return handler_input.response_builder.response


@sb.request_handler(can_handle_func=lambda input: True)
def unhandled_intent_handler(handler_input):
    """Handler for all other unhandled requests."""
    # type: (HandlerInput) -> Response
    speech = "Try asking something about the grid."
    handler_input.response_builder.speak(speech).ask(speech)
    return handler_input.response_builder.response


@sb.exception_handler(can_handle_func=lambda i, e: True)
def all_exception_handler(handler_input, exception):
    """Catch all exception handler, log exception and
    respond with custom message.
    """
    # type: (HandlerInput, Exception) -> Response
    logger.error(exception, exc_info=True)
    speech = "Sorry, I can't understand that. Please try again."
    handler_input.response_builder.speak(speech).ask(speech)
    return handler_input.response_builder.response


@sb.global_response_interceptor()
def log_response(handler_input, response):
    """Response logger."""
    # type: (HandlerInput, Response) -> None
    logger.info("Response: {}".format(response))


lambda_handler = sb.lambda_handler()
