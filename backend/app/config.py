"""All tunables in one place, read from the environment.

Source URLs are configurable on purpose: pointing one at a dead host is the
quickest way to watch the failure path (see README, "Break a source").
"""
import os

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://assess:assess@localhost:5432/assess"
)

# Public services ask to be identified. Include a way to reach a human.
USER_AGENT = os.getenv(
    "USER_AGENT",
    "location-assessment-tool/0.1 (internal prototype; contact: vishal0639@gmail.com)",
)

CENSUS_GEOCODER_URL = os.getenv(
    "CENSUS_GEOCODER_URL",
    "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress",
)
USGS_EPQS_URL = os.getenv("USGS_EPQS_URL", "https://epqs.nationalmap.gov/v1/json")
OPEN_METEO_URL = os.getenv(
    "OPEN_METEO_URL", "https://archive-api.open-meteo.com/v1/archive"
)
FEMA_NFHL_URL = os.getenv(
    "FEMA_NFHL_URL",
    "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query",
)

# Per-source timeouts in seconds. A slow source costs at most this long.
TIMEOUTS = {
    "census_geocoder": float(os.getenv("CENSUS_TIMEOUT", "15")),
    "usgs_epqs": float(os.getenv("USGS_TIMEOUT", "10")),
    "open_meteo": float(os.getenv("OPEN_METEO_TIMEOUT", "15")),
    "fema_nfhl": float(os.getenv("FEMA_TIMEOUT", "20")),
}

WORKER_POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "1"))
# A run claimed longer ago than this is assumed to belong to a dead worker.
RUN_LEASE_SECONDS = int(os.getenv("RUN_LEASE_SECONDS", "300"))
MAX_RUN_ATTEMPTS = int(os.getenv("MAX_RUN_ATTEMPTS", "3"))
