"""
Configuration — resolved per-setting in this priority order:

  1. settings.json in the data dir (values saved from the web UI /settings)
  2. environment variables (/etc/plane-tracker.env via systemd, or .env for
     local development via python-dotenv)
  3. built-in defaults

Existing installs are unaffected until a value is saved in the UI: with no
settings.json (or a blank value for a key), the environment value is used
exactly as before. Values in settings.json are stored as strings and parsed
identically to env vars.

See .env.example for documentation of all available variables.
"""
import json
import os

# Load .env file if present (for local dev; systemd uses EnvironmentFile instead)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
except ImportError:
    pass

# UI-saved settings (web /settings page). Read-only here; the web app writes it.
SETTINGS_FILE = os.path.join(
    os.environ.get("PLANE_TRACKER_DATA_DIR", "/var/lib/plane-tracker"),
    "settings.json",
)


def _load_ui_settings():
    """Load UI settings as {KEY: str-value}; blank values are dropped so they
    fall through to the environment. Never raises."""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return {}
        return {
            str(k): str(v)
            for k, v in raw.items()
            if v is not None and str(v).strip() != ""
        }
    except (FileNotFoundError, json.JSONDecodeError, PermissionError, OSError):
        return {}


_ui = _load_ui_settings()


def _get(name: str, default: str = "") -> str:
    """UI settings first, then environment, then default."""
    if name in _ui:
        return _ui[name]
    return os.environ.get(name, default)


def _bool(val: str) -> bool:
    return val.strip().lower() in ("true", "1", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(str(_get(name, str(default))).strip())
    except (ValueError, TypeError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(str(_get(name, str(default))).strip())
    except (ValueError, TypeError):
        return default


def _require(name: str) -> str:
    """Return setting value or empty string (caller decides how to handle missing)."""
    return _get(name, "")


# --- API Keys ---
FR24_API_KEY = _require("FR24_API_KEY")
TOMORROW_API_KEY = _require("TOMORROW_API_KEY")
AIRLABS_API_KEY = _get("AIRLABS_API_KEY", "")

# --- Bounding box for overhead flight detection ---
ZONE_HOME = {
    "tl_y": _float("ZONE_TL_LAT", 0.0),
    "tl_x": _float("ZONE_TL_LON", 0.0),
    "br_y": _float("ZONE_BR_LAT", 0.0),
    "br_x": _float("ZONE_BR_LON", 0.0),
}

# --- Home location (for distance calculations) ---
LOCATION_HOME = [
    _float("HOME_LAT", 0.0),
    _float("HOME_LON", 0.0),
]

# --- Weather ---
TEMPERATURE_LOCATION = _require("TEMPERATURE_LOCATION")
TEMPERATURE_UNITS = _get("TEMPERATURE_UNITS", "metric")
FORECAST_DAYS = _int("FORECAST_DAYS", 3)

# --- Display & units ---
DISTANCE_UNITS = _get("DISTANCE_UNITS", "metric")
CLOCK_FORMAT = _get("CLOCK_FORMAT", "24hr")
BRIGHTNESS = _int("BRIGHTNESS", 100)
BRIGHTNESS_NIGHT = _int("BRIGHTNESS_NIGHT", 50)
NIGHT_BRIGHTNESS = _bool(_get("NIGHT_BRIGHTNESS", "False"))
NIGHT_START = _get("NIGHT_START", "22:00")
NIGHT_END = _get("NIGHT_END", "06:00")
GPIO_SLOWDOWN = _int("GPIO_SLOWDOWN", 2)
HAT_PWM_ENABLED = _bool(_get("HAT_PWM_ENABLED", "True"))
BONNET_TYPE = _get("BONNET_TYPE", "single").lower()

# --- Flight filtering ---
MIN_ALTITUDE = _int("MIN_ALTITUDE", 0)
JOURNEY_CODE_SELECTED = _require("JOURNEY_CODE_SELECTED")
_raw_filler = _get("JOURNEY_BLANK_FILLER", "").strip()
JOURNEY_BLANK_FILLER = f" {_raw_filler} " if _raw_filler else " ? "
SPEED_UNITS = _get("SPEED_UNITS", "metric")

# --- Hourly chime (played by systemd timer, see setup/systemd/) ---
HOURLY_CHIME_ENABLED = _bool(_get("HOURLY_CHIME_ENABLED", "False"))
HOURLY_CHIME_VOLUME = _int("HOURLY_CHIME_VOLUME", 50)
HOURLY_CHIME_QUIET_START = _get("HOURLY_CHIME_QUIET_START", "22:00")
HOURLY_CHIME_QUIET_END = _get("HOURLY_CHIME_QUIET_END", "08:00")

# --- Flyover context (landmarks / oceans / national parks) ---
# When enabled, the tracked-flight stats line shows a US National Park the
# aircraft is over, or the body of water when far from any city, instead of
# always the nearest city.
LANDMARKS_ENABLED = _bool(_get("LANDMARKS_ENABLED", "False"))

# --- ISS overhead passes ---
# Requires the optional `ephem` package; uses LOCATION_HOME as the observer.
ISS_ENABLED = _bool(_get("ISS_ENABLED", "False"))

# --- ATC audio (LiveATC.net streaming, auto-tuned to overhead traffic) ---
# Master switch — everything below is inert unless this is True.
ATC_ENABLED = _bool(_get("ATC_ENABLED", "False"))
# Initial mode on first run: "auto" (follow overhead traffic), "manual"
# (stay on ATC_STATION), or "off" (wait for an explicit start via the web
# UI / HomeKit). Runtime changes persist in the data dir and win thereafter.
ATC_MODE = _get("ATC_MODE", "auto")
# Initial LiveATC mount for manual mode (e.g. "kjfk_twr"); blank = auto-pick.
ATC_STATION = _get("ATC_STATION", "")
# Initial audio output: "usb" (mpv -> USB speaker on the Pi),
# "chromecast:<uuid>" / "airplay:<id>" (pick ids from /api/atc/outputs),
# or "browser" (a browser client plays the stream itself).
ATC_OUTPUT = _get("ATC_OUTPUT", "usb")
# Initial volume 0-100.
ATC_VOLUME = _int("ATC_VOLUME", 70)
# Quiet window during which auto mode never starts audio ("HH:MM" each).
# Leave both blank to use NIGHT_START/NIGHT_END.
_atc_qs = _get("ATC_QUIET_START", "").strip()
_atc_qe = _get("ATC_QUIET_END", "").strip()
ATC_QUIET_HOURS = f"{_atc_qs}-{_atc_qe}" if _atc_qs and _atc_qe else ""
# Resume a Pi-side output (usb/cast/airplay) after a service restart.
ATC_AUTO_RESUME = _bool(_get("ATC_AUTO_RESUME", "True"))
# mpv --audio-device override, e.g. "alsa/plughw:CARD=UACDemoV10,DEV=0";
# blank = auto-detect the first USB-audio card.
ATC_USB_DEVICE = _get("ATC_USB_DEVICE", "")
# Extra/corrected LiveATC feeds: comma list of "ICAO/kind/mount[/lat/lon]"
# (kind: twr|app|ctr), merged over the built-in station seed.
ATC_CUSTOM_FEEDS = _get("ATC_CUSTOM_FEEDS", "")
# Port the web app (and its loopback /atc/relay) listens on — 8080 under
# systemd, 6969 in the Docker image.
ATC_RELAY_PORT = _int("ATC_RELAY_PORT", 8080)

# --- Logging & notifications ---
EMAIL = _get("EMAIL", "")
EMAIL_SENDER = _get("EMAIL_SENDER", "flight.tracker.alerts2025@gmail.com")
EMAIL_PASSWORD = _get("EMAIL_PASSWORD", "")
MAX_FARTHEST = _int("MAX_FARTHEST", 3)
MAX_CLOSEST = _int("MAX_CLOSEST", 3)
# Days of daily flight-counter history to keep (flight_counter.json)
STATS_LOG_DAYS = _int("STATS_LOG_DAYS", 90)

# --- Infrastructure ---
# systemd service name shown by the web log viewer
SERVICE_NAME = _get("SERVICE_NAME", "plane-tracker")
