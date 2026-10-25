"""Runtime settings for the UI suite, all driven by environment variables."""
import os

WEB_URL = os.environ.get("AIP_WEB_URL", "http://localhost:3000").rstrip("/")
API_URL = os.environ.get("AIP_API_URL", "http://127.0.0.1:8001").rstrip("/")

# "dev" -> cookie aip_dev=1 ; "pat" -> UI login with AIP_E2E_USER / AIP_E2E_TOKEN
AUTH_MODE = os.environ.get("AIP_AUTH", "dev").lower()
E2E_USER = os.environ.get("AIP_E2E_USER", "")
E2E_TOKEN = os.environ.get("AIP_E2E_TOKEN", "")

# Tests that create/modify data in Snowflake only run when explicitly enabled.
ALLOW_WRITES = os.environ.get("AIP_UI_ALLOW_WRITES", "0") == "1"

# Pages render server-side against Snowflake; give them room.
NAV_TIMEOUT_MS = int(os.environ.get("AIP_UI_NAV_TIMEOUT_MS", "90000"))
EXPECT_TIMEOUT_MS = int(os.environ.get("AIP_UI_EXPECT_TIMEOUT_MS", "30000"))

ARTIFACTS_DIR = os.environ.get(
    "AIP_UI_ARTIFACTS", os.path.join(os.path.dirname(__file__), "artifacts")
)

# Visual regression: rewrite baselines, and max share of changed pixels before failing.
UPDATE_SNAPSHOTS = os.environ.get("AIP_UI_UPDATE_SNAPSHOTS", "0") == "1"
VISUAL_THRESHOLD = float(os.environ.get("AIP_UI_VISUAL_THRESHOLD", "0.02"))
