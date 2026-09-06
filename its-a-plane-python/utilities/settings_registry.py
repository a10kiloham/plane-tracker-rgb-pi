"""
Registry of every user-facing setting: the single source of truth for the
web /settings page and its API.

Each entry describes one key that config.py resolves as
    settings.json (UI)  >  environment (/etc/plane-tracker.env, .env)  >  default.

Types:
    str     free text
    secret  free text, masked when displayed
    int     integer (validated)
    float   decimal number (validated)
    bool    "True"/"False"
    time    "HH:MM"
    choice  one of `choices`

Values are STORED AS STRINGS in settings.json and parsed exactly like env
vars, so a value moved between the env file and the UI behaves identically.
"""

import re

SETTINGS = [
    # --- API Keys ---
    dict(key="FR24_API_KEY", group="API Keys", label="FlightRadar24 API key",
         type="secret", default="",
         help='Subscription key, or "subscription_key|token". Required for flight data.'),
    dict(key="TOMORROW_API_KEY", group="API Keys", label="Tomorrow.io API key",
         type="secret", default="",
         help="Required for weather and forecast (free tier is fine)."),
    dict(key="AIRLABS_API_KEY", group="API Keys", label="AirLabs API key",
         type="secret", default="",
         help="Optional — schedules for tracked flights that aren't airborne yet."),

    # --- Location ---
    dict(key="HOME_LAT", group="Location", label="Home latitude", type="float",
         default="0.0", help="Used for distances and airport-code disambiguation."),
    dict(key="HOME_LON", group="Location", label="Home longitude", type="float",
         default="0.0", help=""),
    dict(key="ZONE_TL_LAT", group="Location", label="Zone north edge (lat)",
         type="float", default="0.0",
         help="Bounding box for overhead detection — top-left corner latitude."),
    dict(key="ZONE_TL_LON", group="Location", label="Zone west edge (lon)",
         type="float", default="0.0", help="Top-left corner longitude."),
    dict(key="ZONE_BR_LAT", group="Location", label="Zone south edge (lat)",
         type="float", default="0.0", help="Bottom-right corner latitude."),
    dict(key="ZONE_BR_LON", group="Location", label="Zone east edge (lon)",
         type="float", default="0.0", help="Bottom-right corner longitude."),
    dict(key="JOURNEY_CODE_SELECTED", group="Location", label="Home airport code",
         type="str", default="", help="Your local airport IATA code (e.g. ORD)."),

    # --- Weather ---
    dict(key="TEMPERATURE_LOCATION", group="Weather", label="Weather location",
         type="str", default="",
         help='"lat,lon" for weather lookups — usually the same as home.'),
    dict(key="TEMPERATURE_UNITS", group="Weather", label="Temperature units",
         type="choice", choices=["metric", "imperial"], default="metric",
         help="metric = °C, imperial = °F."),
    dict(key="FORECAST_DAYS", group="Weather", label="Forecast days", type="int",
         default="3", min=1, max=5, help="Days shown on the forecast screen (1-5)."),

    # --- Display ---
    dict(key="BRIGHTNESS", group="Display", label="Brightness", type="int",
         default="100", min=0, max=100, help="LED brightness, 0-100."),
    dict(key="NIGHT_BRIGHTNESS", group="Display", label="Night dimming",
         type="bool", default="False", help="Dim the display during the night window."),
    dict(key="BRIGHTNESS_NIGHT", group="Display", label="Night brightness",
         type="int", default="50", min=0, max=100,
         help="Brightness used when night dimming is active."),
    dict(key="NIGHT_START", group="Display", label="Night starts", type="time",
         default="22:00", help=""),
    dict(key="NIGHT_END", group="Display", label="Night ends", type="time",
         default="06:00", help=""),
    dict(key="CLOCK_FORMAT", group="Display", label="Clock format", type="choice",
         choices=["24hr", "12hr"], default="24hr", help=""),
    dict(key="DISTANCE_UNITS", group="Display", label="Distance units",
         type="choice", choices=["metric", "imperial"], default="metric",
         help="metric = km, imperial = miles."),
    dict(key="SPEED_UNITS", group="Display", label="Speed units", type="choice",
         choices=["metric", "imperial", "knots"], default="metric",
         help="metric = km/h, imperial = mph."),
    dict(key="JOURNEY_BLANK_FILLER", group="Display", label="Unknown-route filler",
         type="str", default="", help='Text shown for an unknown endpoint (default "?").'),

    # --- Flight Filtering ---
    dict(key="MIN_ALTITUDE", group="Flight Filtering", label="Minimum altitude (ft)",
         type="int", default="0", min=0, max=100000,
         help="Ignore aircraft below this altitude (0 = no filter)."),
    dict(key="MAX_CLOSEST", group="Flight Filtering", label="Closest flights kept",
         type="int", default="3", min=1, max=50,
         help="Size of the closest-flight leaderboard."),
    dict(key="MAX_FARTHEST", group="Flight Filtering", label="Farthest flights kept",
         type="int", default="3", min=1, max=50,
         help="Size of the farthest-flight leaderboard."),

    # --- Features ---
    dict(key="ISS_ENABLED", group="Features", label="ISS pass alerts", type="bool",
         default="False",
         help="Alerts and a takeover display when the ISS passes overhead."),
    dict(key="LANDMARKS_ENABLED", group="Features", label="Flyover landmarks",
         type="bool", default="False",
         help="Show parks/oceans on the tracked-flight line instead of only cities."),
    dict(key="HOURLY_CHIME_ENABLED", group="Features", label="Hourly chime",
         type="bool", default="False",
         help="Needs mpv, a speaker, and the systemd timer (setup/systemd/)."),
    dict(key="HOURLY_CHIME_VOLUME", group="Features", label="Chime volume",
         type="int", default="50", min=0, max=100, help=""),
    dict(key="HOURLY_CHIME_QUIET_START", group="Features",
         label="Chime quiet from", type="time", default="22:00", help=""),
    dict(key="HOURLY_CHIME_QUIET_END", group="Features", label="Chime quiet until",
         type="time", default="08:00", help=""),

    # --- ATC Audio ---
    dict(key="ATC_ENABLED", group="ATC Audio", label="ATC audio", type="bool",
         default="False", help="LiveATC streaming — controls appear on the /atc page."),
    dict(key="ATC_MODE", group="ATC Audio", label="Startup mode", type="choice",
         choices=["auto", "manual", "off"], default="auto",
         help="auto follows overhead traffic; manual stays on the station below."),
    dict(key="ATC_STATION", group="ATC Audio", label="Manual station", type="str",
         default="", help='LiveATC mount for manual mode (e.g. "kjfk_twr").'),
    dict(key="ATC_OUTPUT", group="ATC Audio", label="Audio output", type="str",
         default="usb",
         help='"usb", "browser", "chromecast:<uuid>" or "airplay:<id>".'),
    dict(key="ATC_VOLUME", group="ATC Audio", label="Volume", type="int",
         default="70", min=0, max=100, help=""),
    dict(key="ATC_QUIET_START", group="ATC Audio", label="Quiet from", type="time",
         default="", allow_blank=True,
         help="Blank = use the display night window."),
    dict(key="ATC_QUIET_END", group="ATC Audio", label="Quiet until", type="time",
         default="", allow_blank=True, help=""),
    dict(key="ATC_AUTO_RESUME", group="ATC Audio", label="Resume after restart",
         type="bool", default="True", help=""),
    dict(key="ATC_USB_DEVICE", group="ATC Audio", label="USB device override",
         type="str", default="", help="Blank = auto-detect the first USB audio card."),
    dict(key="ATC_CUSTOM_FEEDS", group="ATC Audio", label="Custom feeds", type="str",
         default="", help='Comma list of "ICAO/kind/mount[/lat/lon]" extras.'),

    # --- Notifications ---
    dict(key="EMAIL", group="Notifications", label="Alert email to", type="str",
         default="", help="Blank disables email alerts."),
    dict(key="EMAIL_SENDER", group="Notifications", label="Sender Gmail address",
         type="str", default="flight.tracker.alerts2025@gmail.com", help=""),
    dict(key="EMAIL_PASSWORD", group="Notifications", label="Sender app password",
         type="secret", default="", help="Gmail app password for the sender account."),

    # --- Advanced ---
    dict(key="BONNET_TYPE", group="Advanced", label="Bonnet type", type="choice",
         choices=["single", "triple"], default="single",
         help="Adafruit bonnet wiring. Triple needs the 4-8 DIP switch LEFT."),
    dict(key="HAT_PWM_ENABLED", group="Advanced", label="HAT PWM bridge",
         type="bool", default="True", help="Not applicable to the triple bonnet."),
    dict(key="GPIO_SLOWDOWN", group="Advanced", label="GPIO slowdown", type="int",
         default="2", min=0, max=8, help="2 for Pi 3/4, 1 for Pi Zero."),
    dict(key="STATS_LOG_DAYS", group="Advanced", label="Stats history (days)",
         type="int", default="90", min=1, max=3650, help=""),
    dict(key="SERVICE_NAME", group="Advanced", label="systemd service name",
         type="str", default="plane-tracker",
         help="Used by the journal log viewer."),
    dict(key="ATC_RELAY_PORT", group="Advanced", label="Web/relay port", type="int",
         default="8080", min=1, max=65535,
         help="Port this web app listens on (8080 systemd, 6969 Docker)."),
]

GROUP_ORDER = ["API Keys", "Location", "Weather", "Display", "Flight Filtering",
               "Features", "ATC Audio", "Notifications", "Advanced"]

_BY_KEY = {s["key"]: s for s in SETTINGS}

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def get(key):
    return _BY_KEY.get(key)


def validate(key, value):
    """Validate a string value for a registry key.
    Returns (normalized_value, error_or_None). Empty string is always valid —
    it clears the UI value so the environment/default applies."""
    spec = _BY_KEY.get(key)
    if spec is None:
        return value, "unknown setting"
    value = str(value).strip()
    if value == "":
        return "", None
    t = spec["type"]
    if t == "int":
        try:
            n = int(value)
        except ValueError:
            return value, "must be a whole number"
        if "min" in spec and n < spec["min"]:
            return value, f"must be at least {spec['min']}"
        if "max" in spec and n > spec["max"]:
            return value, f"must be at most {spec['max']}"
        return str(n), None
    if t == "float":
        try:
            float(value)
        except ValueError:
            return value, "must be a number"
        return value, None
    if t == "bool":
        if value.lower() in ("true", "1", "yes", "on"):
            return "True", None
        if value.lower() in ("false", "0", "no", "off"):
            return "False", None
        return value, "must be True or False"
    if t == "time":
        if not _TIME_RE.match(value):
            return value, "must be HH:MM"
        return value, None
    if t == "choice":
        if value not in spec.get("choices", []):
            return value, f"must be one of: {', '.join(spec.get('choices', []))}"
        return value, None
    # str / secret — anything goes
    return value, None


def mask_secret(value):
    """Mask a secret for display, keeping just enough to recognize it."""
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return value[:4] + "•" * 6 + value[-4:]
