"""
Configuration — all values sourced exclusively from environment variables.

NO user-configurable defaults are stored in this file.
All configuration must be provided via:
  - /etc/plane-tracker.env (systemd EnvironmentFile for production)
  - .env file in the project root (for local development via python-dotenv)

See .env.example for documentation of all available variables and their defaults.
"""
import os

# Load .env file if present (for local dev; systemd uses EnvironmentFile instead)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
except ImportError:
    pass


def _bool(val: str) -> bool:
    return val.strip().lower() in ("true", "1", "yes", "on")


def _require(name: str) -> str:
    """Return env var value or empty string (caller decides how to handle missing)."""
    return os.environ.get(name, "")


# --- API Keys ---
FR24_API_KEY = _require("FR24_API_KEY")
TOMORROW_API_KEY = _require("TOMORROW_API_KEY")
AIRLABS_API_KEY = os.environ.get("AIRLABS_API_KEY", "")

# --- Bounding box for overhead flight detection ---
ZONE_HOME = {
    "tl_y": float(os.environ["ZONE_TL_LAT"]) if "ZONE_TL_LAT" in os.environ else 0.0,
    "tl_x": float(os.environ["ZONE_TL_LON"]) if "ZONE_TL_LON" in os.environ else 0.0,
    "br_y": float(os.environ["ZONE_BR_LAT"]) if "ZONE_BR_LAT" in os.environ else 0.0,
    "br_x": float(os.environ["ZONE_BR_LON"]) if "ZONE_BR_LON" in os.environ else 0.0,
}

# --- Home location (for distance calculations) ---
LOCATION_HOME = [
    float(os.environ["HOME_LAT"]) if "HOME_LAT" in os.environ else 0.0,
    float(os.environ["HOME_LON"]) if "HOME_LON" in os.environ else 0.0,
]

# --- Weather ---
TEMPERATURE_LOCATION = _require("TEMPERATURE_LOCATION")
TEMPERATURE_UNITS = os.environ.get("TEMPERATURE_UNITS", "metric")
FORECAST_DAYS = int(os.environ.get("FORECAST_DAYS", "3"))

# --- Display & units ---
DISTANCE_UNITS = os.environ.get("DISTANCE_UNITS", "metric")
CLOCK_FORMAT = os.environ.get("CLOCK_FORMAT", "24hr")
BRIGHTNESS = int(os.environ.get("BRIGHTNESS", "100"))
BRIGHTNESS_NIGHT = int(os.environ.get("BRIGHTNESS_NIGHT", "50"))
NIGHT_BRIGHTNESS = _bool(os.environ.get("NIGHT_BRIGHTNESS", "False"))
NIGHT_START = os.environ.get("NIGHT_START", "22:00")
NIGHT_END = os.environ.get("NIGHT_END", "06:00")
GPIO_SLOWDOWN = int(os.environ.get("GPIO_SLOWDOWN", "2"))
HAT_PWM_ENABLED = _bool(os.environ.get("HAT_PWM_ENABLED", "True"))

# --- Flight filtering ---
MIN_ALTITUDE = int(os.environ.get("MIN_ALTITUDE", "0"))
JOURNEY_CODE_SELECTED = _require("JOURNEY_CODE_SELECTED")
_raw_filler = os.environ.get("JOURNEY_BLANK_FILLER", "").strip()
JOURNEY_BLANK_FILLER = f" {_raw_filler} " if _raw_filler else " ? "
SPEED_UNITS = os.environ.get("SPEED_UNITS", "metric")

# --- Hourly chime (played by systemd timer, see setup/systemd/) ---
HOURLY_CHIME_ENABLED = _bool(os.environ.get("HOURLY_CHIME_ENABLED", "False"))
HOURLY_CHIME_VOLUME = int(os.environ.get("HOURLY_CHIME_VOLUME", "50"))
HOURLY_CHIME_QUIET_START = os.environ.get("HOURLY_CHIME_QUIET_START", "22:00")
HOURLY_CHIME_QUIET_END = os.environ.get("HOURLY_CHIME_QUIET_END", "08:00")

# --- Flyover context (landmarks / oceans / national parks) ---
# When enabled, the tracked-flight stats line shows a US National Park the
# aircraft is over, or the body of water when far from any city, instead of
# always the nearest city.
LANDMARKS_ENABLED = _bool(os.environ.get("LANDMARKS_ENABLED", "False"))

# --- ISS overhead passes ---
# Requires the optional `ephem` package; uses LOCATION_HOME as the observer.
ISS_ENABLED = _bool(os.environ.get("ISS_ENABLED", "False"))

# --- ATC audio (LiveATC.net streaming, auto-tuned to overhead traffic) ---
# Master switch — everything below is inert unless this is True.
ATC_ENABLED = _bool(os.environ.get("ATC_ENABLED", "False"))
# Initial mode on first run: "auto" (follow overhead traffic), "manual"
# (stay on ATC_STATION), or "off" (wait for an explicit start via the web
# UI / HomeKit). Runtime changes persist in the data dir and win thereafter.
ATC_MODE = os.environ.get("ATC_MODE", "auto")
# Initial LiveATC mount for manual mode (e.g. "kjfk_twr"); blank = auto-pick.
ATC_STATION = os.environ.get("ATC_STATION", "")
# Initial audio output: "usb" (mpv -> USB speaker on the Pi),
# "chromecast:<uuid>" / "airplay:<id>" (pick ids from /api/atc/outputs),
# or "browser" (a browser client plays the stream itself).
ATC_OUTPUT = os.environ.get("ATC_OUTPUT", "usb")
# Initial volume 0-100.
ATC_VOLUME = int(os.environ.get("ATC_VOLUME", "70"))
# Quiet window during which auto mode never starts audio ("HH:MM" each).
# Leave both blank to use NIGHT_START/NIGHT_END.
_atc_qs = os.environ.get("ATC_QUIET_START", "").strip()
_atc_qe = os.environ.get("ATC_QUIET_END", "").strip()
ATC_QUIET_HOURS = f"{_atc_qs}-{_atc_qe}" if _atc_qs and _atc_qe else ""
# Resume a Pi-side output (usb/cast/airplay) after a service restart.
ATC_AUTO_RESUME = _bool(os.environ.get("ATC_AUTO_RESUME", "True"))
# mpv --audio-device override, e.g. "alsa/plughw:CARD=UACDemoV10,DEV=0";
# blank = auto-detect the first USB-audio card.
ATC_USB_DEVICE = os.environ.get("ATC_USB_DEVICE", "")
# Extra/corrected LiveATC feeds: comma list of "ICAO/kind/mount[/lat/lon]"
# (kind: twr|app|ctr), merged over the built-in station seed.
ATC_CUSTOM_FEEDS = os.environ.get("ATC_CUSTOM_FEEDS", "")
# Port the web app (and its loopback /atc/relay) listens on — 8080 under
# systemd, 6969 in the Docker image.
ATC_RELAY_PORT = int(os.environ.get("ATC_RELAY_PORT", "8080"))

# --- Logging & notifications ---
EMAIL = os.environ.get("EMAIL", "")
MAX_FARTHEST = int(os.environ.get("MAX_FARTHEST", "3"))
MAX_CLOSEST = int(os.environ.get("MAX_CLOSEST", "3"))
# Days of daily flight-counter history to keep (flight_counter.json)
STATS_LOG_DAYS = int(os.environ.get("STATS_LOG_DAYS", "90"))
