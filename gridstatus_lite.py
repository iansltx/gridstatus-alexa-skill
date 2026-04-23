"""
GridStatus.io API client using Python's stdlib urllib.

Uses ``urllib.request`` for HTTPS — no third-party libraries required.
Replaces the former ``gridstatusio`` + ``requests`` dependency pair while
keeping the exact ``get_dataset()`` interface expected by ``api.py``.

Public surface
--------------
``GridStatusClient(api_key)``
    Thin wrapper around the GridStatus v1 REST API.  The only method used
    by this skill is ``get_dataset()``, which returns a ``list[dict]`` with
    UTC-aware ``datetime`` objects in the interval timestamp columns.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_API_BASE: str = "https://api.gridstatus.io/v1"
_DATASET_QUERY_PATH: str = "/datasets/{dataset}/query"

# Seconds to wait for the server to send a response.
_TIMEOUT: float = 60.0

# Columns whose raw string values should be parsed into UTC-aware datetimes.
_DATETIME_COLS: frozenset[str] = frozenset({"interval_start_utc", "interval_end_utc"})


# ---------------------------------------------------------------------------
# Datetime helper
# ---------------------------------------------------------------------------


def _parse_dt(value: Any) -> Any:
    """
    Coerce an ISO-8601 UTC string to a timezone-aware :class:`datetime`.

    Returns the original value unchanged when it is not a string or cannot
    be parsed, so numeric values and ``None`` flow through without error.
    """
    if not isinstance(value, str):
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except (ValueError, TypeError):
        return value


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class GridStatusClient:
    """
    Minimal GridStatus.io REST API client backed by ``urllib.request``.

    Satisfies the ``get_dataset()`` interface already used by ``api.py``::

        client.get_dataset(
            dataset="ercot_fuel_mix",
            start="2024-01-01T00:00:00+00:00",
            end="2024-01-01T02:00:00+00:00",
            limit=20,
            return_format="python",
            verbose=False,
        )

        client.get_dataset(
            dataset="eia_grid_monitor",
            start=..., end=...,
            filter_column="balancing_authority",
            filter_value="TVA",
            limit=100,
            return_format="python",
            verbose=False,
        )

    ``api.py`` requires zero changes.
    """

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError(
                "A GridStatus API key is required.  Pass api_key= explicitly "
                "or set the GRIDSTATUS_API_KEY environment variable before "
                "constructing GridStatusClient."
            )
        self.api_key = api_key

    def __repr__(self) -> str:
        return f"GridStatusClient(host={_API_BASE})"

    # ---------------------------------------------------------------------- #
    # Core method                                                             #
    # ---------------------------------------------------------------------- #

    def get_dataset(
        self,
        dataset: str,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
        return_format: str = "python",
        verbose: bool = True,
        filter_column: str | None = None,
        filter_value: str | int | None = None,
        filter_operator: str = "=",
    ) -> list[dict]:
        """
        Fetch rows from a GridStatus dataset over HTTPS.

        Parameters
        ----------
        dataset:
            Dataset identifier, e.g. ``"ercot_fuel_mix"``.
        start:
            ISO-8601 start time (inclusive), e.g. ``"2024-01-01T00:00:00+00:00"``.
        end:
            ISO-8601 end time (exclusive).
        limit:
            Maximum number of rows to return.
        return_format:
            ``"python"`` (default) → ``interval_*_utc`` columns are converted
            to UTC-aware :class:`datetime` objects.
            Any other value → raw strings are returned as-is.
        verbose:
            When ``True``, logs the full request URL at INFO level.
        filter_column:
            Column name to filter on, e.g. ``"balancing_authority"``.
        filter_value:
            Value to match in *filter_column*.
        filter_operator:
            Comparison operator; defaults to ``"="``.

        Returns
        -------
        list[dict]
            One dict per row.  Returns an empty list when no rows match.

        Raises
        ------
        RuntimeError
            On HTTP errors (non-200 status) or connection failures.
        """
        # ------------------------------------------------------------------ #
        # Build query string                                                  #
        # ------------------------------------------------------------------ #
        # "array-of-arrays" schema: data[0] is the column-name list;
        # data[1:] are the value rows.  This is the same wire format the
        # former gridstatusio library requested internally.
        params: dict[str, str] = {
            "return_format": "json",
            "json_schema": "array-of-arrays",
        }

        if start is not None:
            params["start_time"] = start
        if end is not None:
            params["end_time"] = end
        if limit is not None:
            params["limit"] = str(limit)
        if filter_column is not None:
            params["filter_column"] = filter_column
            params["filter_value"] = "" if filter_value is None else str(filter_value)
            params["filter_operator"] = filter_operator

        url = (
            f"{_API_BASE}{_DATASET_QUERY_PATH.format(dataset=dataset)}"
            f"?{urllib.parse.urlencode(params)}"
        )

        if verbose:
            logger.info("GET %s", url)

        # ------------------------------------------------------------------ #
        # Execute request                                                     #
        # ------------------------------------------------------------------ #
        req = urllib.request.Request(
            url,
            headers={
                "x-api-key": self.api_key,
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                body: bytes = resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail: str = json.loads(raw).get("detail", raw)
            except Exception:
                detail = raw
            raise RuntimeError(
                f"GridStatus API HTTP {exc.code} for dataset '{dataset}': {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"GridStatus API connection error for dataset '{dataset}': {exc.reason}"
            ) from exc

        # ------------------------------------------------------------------ #
        # Parse JSON payload                                                  #
        # ------------------------------------------------------------------ #
        try:
            payload: dict = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"GridStatus API returned non-JSON body for dataset '{dataset}': {exc}"
            ) from exc

        raw_data: list = payload.get("data", [])

        # Need at least a header row plus one data row.
        if len(raw_data) < 2:
            return []

        col_names: list[str] = raw_data[0]
        records: list[dict] = [dict(zip(col_names, row)) for row in raw_data[1:]]

        if return_format != "python":
            return records

        # ------------------------------------------------------------------ #
        # Convert datetime columns to UTC-aware datetime objects             #
        # ------------------------------------------------------------------ #
        return [
            {k: (_parse_dt(v) if k in _DATETIME_COLS else v) for k, v in rec.items()}
            for rec in records
        ]
