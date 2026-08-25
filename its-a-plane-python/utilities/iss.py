"""
iss.py — ISS overhead pass predictions.

Computes passes locally using TLE orbital data from CelesTrak + the
ephem library.  No external pass-prediction API needed.  TLE is refreshed
at most every 12 hours (or on first run); pass computation runs every
30 minutes and takes <1 second even on a Pi Zero.

Ported from ajplotkin's iss-visibility-indicator branch, adapted to this
fork's house rules:
  - Feature-gated by config.ISS_ENABLED (env var, default off).
  - Observer location comes from config.LOCATION_HOME (HOME_LAT/HOME_LON).
  - Persistent caches live in .cache/ and are written atomically
    (tmp file + os.replace) like utilities/temperature.py.
  - `ephem` is an optional dependency: without it the module imports fine
    and the feature simply stays disabled.
  - All public entry points are non-blocking: computation happens in a
    daemon thread; callers always get the current cache immediately.

Usage:
    from utilities.iss import get_iss_alert, get_iss_pass_data
    alert = get_iss_alert()
    # {"text": "ISS 3m", "color": "white", "visible": bool}  or  None
    active = get_iss_pass_data()
    # dict with progress/countdown/compass info while a pass is overhead,
    # else None
"""

import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone, timedelta

import requests

try:
    import ephem
except ImportError:
    ephem = None

logger = logging.getLogger(__name__)

_TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE"
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
_CACHE_FILE = os.path.join(_CACHE_DIR, "iss.json")
_TLE_CACHE_FILE = os.path.join(_CACHE_DIR, "iss_tle.json")
_POLL_INTERVAL = 1800      # recompute passes every 30 minutes
_TLE_REFRESH = 43200       # refresh TLE at most every 12 hours
_ALERT_WINDOW = 600        # show alert 10 minutes before pass
_MIN_ELEVATION = 10        # minimum max-elevation to include a pass (degrees)
_PREDICT_HOURS = 24        # how far ahead to predict

# Compass direction from azimuth
_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def _az_to_compass(az_rad):
    """Convert azimuth in radians to 16-point compass direction."""
    deg = math.degrees(float(az_rad)) % 360
    idx = int((deg + 11.25) / 22.5) % 16
    return _COMPASS[idx]


def _atomic_write_json(path, obj):
    """Write JSON atomically (tmp + os.replace) so a crash mid-write can't
    leave truncated JSON behind. Never raises."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f)
        os.replace(tmp, path)
    except (PermissionError, OSError) as e:
        logger.warning(f"[ISS] Cannot write cache {path}: {e}")


def _enabled():
    """Feature gate: ISS_ENABLED set, ephem importable, home location set."""
    if ephem is None:
        return False
    try:
        import config as cfg
        return bool(getattr(cfg, "ISS_ENABLED", False)) and (
            list(cfg.LOCATION_HOME) != [0.0, 0.0]
        )
    except Exception:
        return False


def _home_location():
    import config as cfg
    return cfg.LOCATION_HOME


def _is_visible(obs, iss_body, dt):
    """Check if ISS is visible: observer in twilight/night AND ISS sunlit.

    Visibility requires two conditions:
    1. Sun is below -6 degrees for the observer (at least civil twilight)
    2. ISS is NOT in Earth's shadow (still catching sunlight)
    """
    obs.date = ephem.Date(dt)
    sun = ephem.Sun(obs)
    iss_body.compute(obs)
    sun_alt_deg = math.degrees(float(sun.alt))
    if sun_alt_deg > -6.0:
        return False
    return not iss_body.eclipsed


# ── TLE management ──────────────────────────────────────────────────────────

_tle_lines = None   # (name, line1, line2)
_tle_ts = 0.0
_tle_fetch_failed_until = 0.0   # don't hammer CelesTrak after a failure


def _load_tle_cache():
    """Load TLE from disk cache if fresh enough."""
    global _tle_lines, _tle_ts
    try:
        with open(_TLE_CACHE_FILE, "r") as f:
            obj = json.load(f)
        if time.time() - obj.get("ts", 0) < _TLE_REFRESH * 2:
            _tle_lines = (obj["name"], obj["line1"], obj["line2"])
            _tle_ts = obj["ts"]
            return True
    except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError):
        pass
    return False


def _fetch_tle():
    """Download current TLE from CelesTrak (guarded, at most every 12h)."""
    global _tle_lines, _tle_ts, _tle_fetch_failed_until
    if time.time() < _tle_fetch_failed_until:
        return False
    try:
        r = requests.get(_TLE_URL, timeout=(5, 15))
        r.raise_for_status()
        lines = r.text.strip().split("\n")
        if len(lines) >= 3:
            name = lines[0].strip()
            line1 = lines[1].strip()
            line2 = lines[2].strip()
            _tle_lines = (name, line1, line2)
            _tle_ts = time.time()
            _atomic_write_json(_TLE_CACHE_FILE, {
                "ts": _tle_ts, "name": name, "line1": line1, "line2": line2,
            })
            logger.info(f"[ISS] TLE updated: {name}")
            return True
        logger.error("[ISS] TLE fetch returned unexpected payload")
    except Exception as e:
        logger.error(f"[ISS] TLE fetch failed: {e}")
    # Back off 15 minutes before trying CelesTrak again
    _tle_fetch_failed_until = time.time() + 900
    return False


def _ensure_tle():
    """Make sure we have a TLE, fetching if needed. Serves a stale cached
    TLE rather than nothing when the network is down."""
    if _tle_lines and (time.time() - _tle_ts) < _TLE_REFRESH:
        return True
    if _load_tle_cache():
        if (time.time() - _tle_ts) < _TLE_REFRESH:
            return True
    if _fetch_tle():
        return True
    # Fetch failed — a stale TLE (up to 24h old, from _load_tle_cache) is
    # still good enough for pass prediction.
    return _tle_lines is not None


# ── Pass computation ────────────────────────────────────────────────────────

def _compute_passes(lat, lon):
    """Compute ISS passes for the next _PREDICT_HOURS hours using ephem.

    Returns a list of dicts:
        [{"rise": {"time": "...", "compass": "NW"},
          "set":  {"time": "...", "compass": "SE"},
          "culmination": {"elevation_deg": 45.2},
          "duration_sec": 340,
          "visible": bool}, ...]
    or None on failure.
    """
    if ephem is None:
        logger.error("[ISS] ephem not installed — pip install ephem")
        return None
    if not _ensure_tle():
        return None

    name, line1, line2 = _tle_lines
    iss = ephem.readtle(name, line1, line2)

    obs = ephem.Observer()
    obs.lat = str(lat)
    obs.lon = str(lon)
    obs.elevation = 10
    obs.horizon = "0"  # compute from true horizon; filter by max_elev later

    now_utc = datetime.now(timezone.utc)
    obs.date = ephem.Date(now_utc)
    end_date = ephem.Date(now_utc + timedelta(hours=_PREDICT_HOURS))

    passes = []
    for _ in range(30):  # safety cap
        try:
            info = obs.next_pass(iss)
            rise_time, rise_az, max_time, max_alt, set_time, set_az = info
            if rise_time is None or rise_time > end_date:
                break
            # At polar latitudes ephem may return None for set/max fields
            if set_time is None or max_alt is None:
                obs.date = (rise_time or obs.date) + ephem.minute
                continue

            max_elev = math.degrees(float(max_alt))
            if max_elev < _MIN_ELEVATION:
                obs.date = set_time + ephem.minute
                continue

            rise_dt = ephem.Date(rise_time).datetime().replace(tzinfo=timezone.utc)
            set_dt = ephem.Date(set_time).datetime().replace(tzinfo=timezone.utc)
            duration = (set_dt - rise_dt).total_seconds()

            # Check visibility at culmination (fresh copies to avoid
            # mutating the iteration observer)
            vis_obs = ephem.Observer()
            vis_obs.lat, vis_obs.lon = obs.lat, obs.lon
            vis_obs.elevation = obs.elevation
            vis_iss = ephem.readtle(name, line1, line2)
            visible = _is_visible(vis_obs, vis_iss, max_time)

            passes.append({
                "rise": {
                    "time": rise_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "compass": _az_to_compass(rise_az),
                },
                "set": {
                    "time": set_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "compass": _az_to_compass(set_az),
                },
                "culmination": {
                    "elevation_deg": round(max_elev, 1),
                },
                "duration_sec": int(duration),
                "visible": visible,
            })
            obs.date = set_time + ephem.minute
        except Exception as e:
            logger.error(f"[ISS] Pass computation error: {e}")
            break

    _atomic_write_json(_CACHE_FILE, {"ts": time.time(), "passes": passes})

    vis_count = sum(1 for p in passes if p.get("visible"))
    logger.info(f"[ISS] Computed {len(passes)} passes (>={_MIN_ELEVATION} deg, "
                f"{vis_count} visible) for next {_PREDICT_HOURS}h")
    return passes


# ── Caching & threading (same pattern as other utility modules) ─────────────

_cached_passes = None
_cached_ts = 0.0
_next_retry_after = 0.0
_consecutive_failures = 0
_refresh_lock = threading.Lock()
_refresh_pending = False


def _load_cache(max_age=None):
    """Load from disk cache. Returns (passes, ts) or (None, 0).

    :param max_age: maximum acceptable age in seconds
                    (default: 2x the poll interval).
    """
    if max_age is None:
        max_age = _POLL_INTERVAL * 2
    try:
        with open(_CACHE_FILE, "r") as f:
            obj = json.load(f)
        ts = obj.get("ts", 0)
        if time.time() - ts < max_age:
            return obj.get("passes", []), ts
    except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError):
        pass
    return None, 0


def _background_compute(lat, lon):
    """Compute ISS passes in a background thread so the display never blocks."""
    global _cached_passes, _cached_ts, _next_retry_after, \
        _consecutive_failures, _refresh_pending
    with _refresh_lock:
        try:
            passes = _compute_passes(lat, lon)
            now = time.time()
            if passes is not None:
                _cached_passes = passes
                _cached_ts = now
                _consecutive_failures = 0
                _next_retry_after = now + _POLL_INTERVAL
            else:
                _consecutive_failures += 1
                backoff = min(_POLL_INTERVAL * (2 ** _consecutive_failures), 14400)
                _next_retry_after = now + backoff
                logger.warning(
                    f"[ISS] Backing off, next retry in {backoff // 60:.0f}m")
                # Serve stale disk cache rather than nothing (predictions
                # stay useful for many hours)
                if _cached_passes is None:
                    stale, stale_ts = _load_cache(max_age=_PREDICT_HOURS * 3600)
                    if stale is not None:
                        _cached_passes = stale
                        _cached_ts = stale_ts
                        logger.info("[ISS] Serving stale disk cache after failure")
        finally:
            _refresh_pending = False


def _refresh():
    """Return cached pass data immediately; kick off a background compute
    if the cache is stale. Never blocks."""
    global _cached_passes, _cached_ts, _next_retry_after, _refresh_pending

    if not _enabled():
        return []
    location = _home_location()

    now = time.time()
    if _cached_passes is not None and now < _next_retry_after:
        return _cached_passes

    # Cold start: try disk cache (non-blocking)
    if _cached_passes is None:
        disk, disk_ts = _load_cache()
        if disk is not None:
            _cached_passes = disk
            _cached_ts = disk_ts
            _next_retry_after = disk_ts + _POLL_INTERVAL
            logger.info("[ISS] Loaded from disk cache")
            if now < _next_retry_after:
                return _cached_passes

    # Schedule non-blocking background compute if interval elapsed
    if now >= _next_retry_after and not _refresh_pending:
        _refresh_pending = True
        threading.Thread(
            target=_background_compute,
            args=(location[0], location[1]),
            daemon=True,
        ).start()

    return _cached_passes or []


# ── Public API ──────────────────────────────────────────────────────────────

def _find_active_pass(passes, now=None):
    """Find a pass currently in progress.
    Returns (pass_dict, seconds_since_rise) or (None, 0)."""
    if now is None:
        now = datetime.now(timezone.utc)
    for p in passes:
        try:
            rise_str = p.get("rise", {}).get("time", "")
            if not rise_str:
                continue
            rise_time = datetime.fromisoformat(rise_str.replace("Z", "+00:00"))
            seconds_since = (now - rise_time).total_seconds()
            duration = p.get("duration_sec", 0)
            if 0 <= seconds_since < duration:
                return p, seconds_since
        except (KeyError, ValueError, TypeError):
            continue
    return None, 0


def get_iss_pass_data():
    """Return detailed pass data when the ISS is actively overhead, else None.

    Also serves as the background pre-warm hook (called from Overhead._grab):
    every call refreshes/computes the pass cache in a non-blocking way.

    Returns dict with keys:
        rise_time, set_time, rise_compass, set_compass, max_elevation,
        duration_sec, progress (0.0-1.0), time_remaining_sec, is_active,
        visible
    """
    passes = _refresh()
    if not passes:
        return None

    active_pass, seconds_since = _find_active_pass(passes)
    if active_pass is None:
        return None

    duration = active_pass.get("duration_sec", 1) or 1
    progress = max(0.0, min(1.0, seconds_since / duration))
    time_remaining = max(0, duration - int(seconds_since))

    return {
        "rise_time": active_pass.get("rise", {}).get("time", ""),
        "set_time": active_pass.get("set", {}).get("time", ""),
        "rise_compass": active_pass.get("rise", {}).get("compass", "?"),
        "set_compass": active_pass.get("set", {}).get("compass", "?"),
        "max_elevation": active_pass.get("culmination", {}).get("elevation_deg", 0),
        "duration_sec": duration,
        "progress": progress,
        "time_remaining_sec": time_remaining,
        "is_active": True,
        "visible": active_pass.get("visible", False),
    }


def get_iss_alert(now=None):
    """Return alert dict if an ISS pass is within 10 minutes, else None.

    Returns {"text": "ISS 3m", "color": "white", "visible": bool} or None.
    When the pass is actively overhead, returns None (the takeover scene
    handles it).
    """
    passes = _refresh()
    if not passes:
        return None

    if now is None:
        now = datetime.now(timezone.utc)

    for p in passes:
        try:
            rise_str = p.get("rise", {}).get("time", "")
            if not rise_str:
                continue
            rise_time = datetime.fromisoformat(rise_str.replace("Z", "+00:00"))
            seconds_until = (rise_time - now).total_seconds()

            if seconds_until < 0:
                # Pass already started — takeover scene handles active passes
                duration = p.get("duration_sec", 0)
                if seconds_until > -duration:
                    return None  # suppress alert while takeover is active
                continue

            if seconds_until <= _ALERT_WINDOW:
                mins = max(1, int(seconds_until / 60))
                visible = p.get("visible", False)
                return {"text": f"ISS {mins}m", "color": "white",
                        "visible": visible}

        except (KeyError, ValueError, TypeError) as e:
            logger.debug(f"[ISS] Skipping pass with parse error: {e}")
            continue

    return None


def is_iss_visible_now(lat, lon):
    """Real-time visibility check for the active-pass display.

    Returns True if the ISS is currently visible (observer in twilight/night
    and ISS is sunlit).  Cheap — single ephem computation, <1ms.
    """
    if ephem is None or not _ensure_tle():
        return False
    try:
        name, line1, line2 = _tle_lines
        iss = ephem.readtle(name, line1, line2)
        obs = ephem.Observer()
        obs.lat, obs.lon = str(lat), str(lon)
        obs.elevation = 10
        return _is_visible(obs, iss, datetime.now(timezone.utc))
    except Exception as e:
        logger.debug(f"[ISS] Visibility check failed: {e}")
        return False


def get_iss_position():
    """Current ISS ground-track position for the web maps.

    Returns {"lat": float, "lon": float, "alt_km": float} or None.
    Uses the cached TLE (fetches at most every 12h). Cheap and safe to call
    from the web process; returns None when the feature is disabled.
    """
    if not _enabled() or not _ensure_tle():
        return None
    try:
        name, line1, line2 = _tle_lines
        iss = ephem.readtle(name, line1, line2)
        iss.compute(datetime.now(timezone.utc))
        return {
            "lat": math.degrees(float(iss.sublat)),
            "lon": math.degrees(float(iss.sublong)),
            "alt_km": float(iss.elevation) / 1000.0,
        }
    except Exception as e:
        logger.debug(f"[ISS] Position computation failed: {e}")
        return None
