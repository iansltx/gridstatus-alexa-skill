"""
FastAPI app for the Grid Status Dialogflow integration.

Routes
------
GET  /                    Serve the web chat UI (bot.html Jinja2 template).
POST /hooks/dialogflow    Dialogflow v2 webhook fulfillment endpoint.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from energy_mix_intent import handle_current_energy_mix
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from gridstatus_lite import GridStatusClient

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Grid Status Dialogflow Bot")
templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "templates")
)

# ---------------------------------------------------------------------------
# GridStatus API client (key read from environment)
# ---------------------------------------------------------------------------
_grid_status_api_key = os.environ.get("GRIDSTATUS_API_KEY", "")
grid_status_client: GridStatusClient | None = (
    GridStatusClient(api_key=_grid_status_api_key) if _grid_status_api_key else None
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_time_str(time_param: Any) -> str | None:
    """Extract ``HH:MM`` from a Dialogflow ``@sys.time`` parameter value.

    Dialogflow sends times as full ISO-8601 timestamps such as
    ``"2024-01-14T15:00:00-08:00"``; we need only the ``HH:MM`` portion so
    that the shared handler can interpret it in the *ISO's* local timezone
    rather than the user's.
    """
    if not time_param:
        return None
    m = re.search(r"T(\d{2}):(\d{2})", str(time_param))
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    return None


def _extract_date_str(date_param: Any) -> str | None:
    """Extract ``YYYY-MM-DD`` from a Dialogflow ``@sys.date`` parameter value.

    Dialogflow sends dates as full ISO-8601 timestamps such as
    ``"2024-01-15T12:00:00-08:00"``; we need only the calendar-date portion.
    """
    if not date_param:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})", str(date_param))
    if m:
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Render the web chat UI, injecting Dialogflow credentials from env."""
    return templates.TemplateResponse(
        "bot.html",
        {
            "request": request,
            "DIALOGFLOW_PROJECT_ID": os.environ.get("DIALOGFLOW_PROJECT_ID", ""),
            "DIALOGFLOW_API_KEY": os.environ.get("DIALOGFLOW_API_KEY", ""),
        },
    )


@app.post("/hooks/dialogflow")
async def dialogflow_webhook(request: Request) -> dict:
    """Dialogflow v2 webhook fulfillment endpoint.

    Dialogflow sends a POST with a JSON body whenever the agent needs
    fulfillment for an intent that has the webhook enabled.  We handle the
    ``CurrentEnergyMix`` intent and pass everything else through with a
    generic fallback response.

    Expected request body (Dialogflow WebhookRequest v2):
        https://cloud.google.com/dialogflow/es/docs/fulfillment-webhook#webhook_request

    Response body (Dialogflow WebhookResponse v2):
        https://cloud.google.com/dialogflow/es/docs/fulfillment-webhook#webhook_response
    """
    body = await request.json()
    query_result = body.get("queryResult", {})
    intent_name = query_result.get("intent", {}).get("displayName", "")
    parameters = query_result.get("parameters", {})

    logger.info("Dialogflow webhook called for intent: %s", intent_name)

    if intent_name == "CurrentEnergyMix":
        if grid_status_client is None:
            return {
                "fulfillmentText": (
                    "The Grid Status API key is not configured on the server. "
                    "Please set the GRIDSTATUS_API_KEY environment variable."
                )
            }

        # Dialogflow passes the canonical entity value directly as the
        # parameter value (already resolved); no slot-resolution loop needed.
        iso: str | None = parameters.get("iso") or None

        # @sys.time and @sys.date arrive as full ISO-8601 timestamps; strip
        # them down to the HH:MM / YYYY-MM-DD substrings the shared handler
        # expects.
        time_str = _extract_time_str(parameters.get("time"))
        date_str = _extract_date_str(parameters.get("date"))

        speech, _reprompt = handle_current_energy_mix(
            grid_status_client, iso, time_str, date_str
        )

        return {
            "fulfillmentText": speech,
            "fulfillmentMessages": [{"text": {"text": [speech]}}],
        }

    # --- Fallback for any other intent ---
    fallback = (
        "I can help you check electricity grid data. "
        "Try asking about the fuel mix or generation mix for a grid like "
        "ERCOT, CAISO, PJM, or ISO New England."
    )
    return {
        "fulfillmentText": fallback,
        "fulfillmentMessages": [{"text": {"text": [fallback]}}],
    }
