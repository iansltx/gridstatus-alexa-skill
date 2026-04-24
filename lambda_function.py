# -*- coding: utf-8 -*-

import logging
import os

import boto3
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_core.skill_builder import CustomSkillBuilder
from ask_sdk_core.utils import is_intent_name, is_request_type
from ask_sdk_dynamodb.adapter import DynamoDbAdapter
from ask_sdk_model import Response

from energy_mix_intent import handle_current_energy_mix
from gridstatus_lite import GridStatusClient

SKILL_NAME = "Grid Status"

ddb_region = os.environ.get("DYNAMODB_PERSISTENCE_REGION")
ddb_table_name = os.environ.get("DYNAMODB_PERSISTENCE_TABLE_NAME")
ddb_resource = boto3.resource("dynamodb", region_name=ddb_region)
dynamodb_adapter = DynamoDbAdapter(
    table_name=ddb_table_name, create_table=False, dynamodb_resource=ddb_resource
)
sb = CustomSkillBuilder(persistence_adapter=dynamodb_adapter)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _load_config() -> dict:
    table = ddb_resource.Table(ddb_table_name)
    response = table.get_item(Key={"id": "config"})
    return response.get("Item", {})


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

    # Delegate all validation, API calls, and response formatting to the
    # shared handler in energy_mix_intent.py.
    speech, reprompt = handle_current_energy_mix(
        grid_status_client, iso, time_str, date_str
    )

    if reprompt:
        handler_input.response_builder.speak(speech).ask(reprompt)
    else:
        handler_input.response_builder.speak(speech)
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
