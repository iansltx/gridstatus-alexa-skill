import logging
from datetime import datetime, timedelta
from datetime import timezone as tz_module

logger = logging.getLogger(__name__)

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


def format_fuel_mix_speech(result, iso_display_name):
    """
    Format a fuel mix result dict into a human-readable Alexa speech string.

    Args:
        result: dict with key 'fuel_mix' (dict mapping fuel type to MW)
        iso_display_name: Human-readable name for the ISO/BA (e.g. "ERCOT")

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

    return f"The energy mix for {iso_display_name} is {mix_str}."
