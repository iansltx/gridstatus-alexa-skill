"""
FastAPI app for the Grid Status Dialogflow integration.

Routes
------
GET  /                              Serve the web chat UI (bot.html Jinja2 template).
POST /hooks/dialogflow              Dialogflow v2 webhook fulfillment endpoint.
POST /sessions/{id}/detectIntent    Proxy detectIntent to Dialogflow, authenticated via
                                    service-account credentials from the
                                    DIALOGFLOW_SERVICE_CREDENTIALS_JSON env var.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import google.auth.transport.requests
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from google.oauth2 import service_account
from starlette.concurrency import run_in_threadpool

from energy_mix_intent import handle_current_energy_mix
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
# Dialogflow service-account credential cache
# ---------------------------------------------------------------------------
# Credentials are loaded once and refreshed automatically when the access
# token expires (typically after one hour).
_df_credentials: service_account.Credentials | None = None

_DIALOGFLOW_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def _get_fresh_token() -> str:
    """Return a valid OAuth2 Bearer token for the Dialogflow service account.

    This function is **synchronous** (google-auth uses the ``requests`` library
    internally) and must be called via ``run_in_threadpool`` from async code so
    it does not block the event loop.

    Credentials are module-level cached and only refreshed when the current
    access token has expired or has not yet been obtained.
    """
    global _df_credentials

    if _df_credentials is None:
        raw = os.environ.get("DIALOGFLOW_SERVICE_CREDENTIALS_JSON", "").strip()
        if not raw:
            raise RuntimeError(
                "DIALOGFLOW_SERVICE_CREDENTIALS_JSON environment variable is not set"
            )
        info = json.loads(raw)
        _df_credentials = service_account.Credentials.from_service_account_info(
            info, scopes=_DIALOGFLOW_SCOPES
        )

    if not _df_credentials.valid:
        auth_req = google.auth.transport.requests.Request()
        _df_credentials.refresh(auth_req)

    return _df_credentials.token  # type: ignore[return-value]


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
    """Render the web chat UI."""
    return templates.TemplateResponse(request, "bot.html")


@app.post("/sessions/{session_id}/detectIntent")
async def detect_intent_proxy(session_id: str, request: Request) -> JSONResponse:
    """Proxy a Dialogflow ES v2 detectIntent call, authenticated server-side.

    The browser sends the same ``queryInput`` payload it would have sent
    directly to Dialogflow.  This route forwards it to:

        POST https://dialogflow.googleapis.com/v2/projects/{project}/agent/sessions/{session}:detectIntent

    authenticated with an OAuth2 Bearer token obtained from the service-account
    credentials stored in ``DIALOGFLOW_SERVICE_CREDENTIALS_JSON``.

    The raw Dialogflow response (including status code) is forwarded back to
    the caller unchanged, so the client-side JavaScript requires no changes
    to how it parses the response.
    """
    project_id = os.environ.get("DIALOGFLOW_PROJECT_ID", "").strip()
    if not project_id:
        raise HTTPException(
            status_code=500,
            detail="DIALOGFLOW_PROJECT_ID environment variable is not set",
        )

    # Obtain / refresh the access token in a thread-pool (synchronous I/O).
    try:
        token = await run_in_threadpool(_get_fresh_token)
    except (RuntimeError, ValueError, KeyError) as exc:
        logger.exception("Failed to obtain Dialogflow service-account token")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    body = await request.json()

    dialogflow_url = (
        f"https://dialogflow.googleapis.com/v2/projects/{project_id}"
        f"/agent/sessions/{session_id}:detectIntent"
    )

    logger.info(
        "Proxying detectIntent to Dialogflow: session=%s project=%s",
        session_id,
        project_id,
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            dialogflow_url,
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )

    # Proxy the response (including non-2xx status codes) back to the caller.
    try:
        resp_body = resp.json()
    except Exception:
        resp_body = {"error": {"message": resp.text}}

    return JSONResponse(content=resp_body, status_code=resp.status_code)


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
