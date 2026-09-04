"""
airports.py — Local airport coordinate lookup.
Downloads airport-codes.csv from GitHub on first run and caches
as airports.json in the project root. Subsequent lookups are instant.

Source: https://github.com/datasets/airport-codes
No API key required. Run once, works offline forever after.

Usage:
    from utilities.airports import get_airport_coords
    coords = get_airport_coords("ORD")  # {"lat": 41.978, "lon": -87.904}
    coords = get_airport_coords("KORD") # same result
"""

import csv
import json
import math
import os
import threading
import time
import requests
from io import StringIO

BASE_DIR    = os.path.dirname(os.path.dirname(__file__))
CACHE_FILE  = os.path.join(BASE_DIR, "airports.json")
CSV_URL     = "https://raw.githubusercontent.com/datasets/airport-codes/master/data/airport-codes.csv"

# Permanent user corrections — survive database rebuilds. Lives in the data
# dir (not the repo) so git operations never touch it.
DATA_DIR = os.environ.get("PLANE_TRACKER_DATA_DIR", "/var/lib/plane-tracker")
OVERRIDES_FILE = os.path.join(DATA_DIR, "airport_overrides.json")

# Cache version — increment to force rebuild (e.g. when coordinate parsing changes)
# v2: confirmed coordinates field is "latitude, longitude" order
# v3: IATA collision resolution (home region prefix > nearest to home),
#     entries carry icao + name, colliding candidates kept for the UI
CACHE_VERSION = 3

# In-memory lookup: both IATA and ICAO -> {lat, lon, icao, name}
_db = {}
_collisions = {}            # iata -> [candidate entries] for codes with >1 airport
_loaded = False
_load_lock = threading.Lock()
_not_found = set()          # codes confirmed missing; cleared on successful refresh
_refresh_pending = False    # True while a background refresh thread is running
_last_refresh_ts = 0.0      # last background refresh time (cooldown guard)
_REFRESH_COOLDOWN = 86400   # at most one background re-download per day


def _home_location():
    """(lat, lon) of the configured home, or None if unset/zero."""
    try:
        from config import LOCATION_HOME
        lat, lon = float(LOCATION_HOME[0]), float(LOCATION_HOME[1])
        if lat or lon:
            return lat, lon
    except Exception:
        pass
    return None


def _dist_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km (build-time scoring only)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _nearest_country(entries, home):
    """iso_country of the airport nearest to home, or None."""
    nearest_c, nearest_d = None, None
    for e in entries:
        c = e.get("country")
        if not c:
            continue
        d = _dist_km(home[0], home[1], e["lat"], e["lon"])
        if nearest_d is None or d < nearest_d:
            nearest_c, nearest_d = c, d
    return nearest_c


def _resolve_iata_collisions(candidates_by_code, home, home_country=None):
    """
    Pick one airport per code when several answer to it — a code can be a
    foreign IATA code AND a US-style local code at once (a flight "to QSI"
    can mean a local field or Moshi, Tanzania).

    Preference order:
      1. A major airport whose real IATA code this is (type=large_airport)
         — "AMS" must mean Schiphol everywhere, never a dirt strip whose
         FAA local code happens to be AMS.
      2. An airport in the home region (same iso_country as the airport
         nearest home) — the user rule for the ambiguous small-field space.
      3. A medium airport's real IATA code.
      4. Nearest to home. Without a home location, a candidate with a real
         4-letter ICAO ident wins.

    Returns (resolved {code: entry}, collisions {code: [entries]}).
    """
    if home and not home_country:
        home_country = _nearest_country(
            (e for cands in candidates_by_code.values() for e in cands), home)

    resolved, collisions = {}, {}
    for code, cands in candidates_by_code.items():
        if len(cands) == 1:
            resolved[code] = cands[0]
            continue

        def score(e):
            typ = e.get("type", "")
            src = e.get("src", "")
            large_iata = (typ == "large_airport" and src == "iata")
            medium_iata = (typ == "medium_airport" and src == "iata")
            in_region = bool(home_country and e.get("country") == home_country)
            d = _dist_km(home[0], home[1], e["lat"], e["lon"]) if home else 0.0
            icao = e.get("icao", "")
            has_real_icao = len(icao) == 4 and icao.isalpha()
            return (0 if large_iata else 1,
                    0 if in_region else 1,
                    0 if medium_iata else 1,
                    d,
                    0 if has_real_icao else 1)

        ranked = sorted(cands, key=score)
        resolved[code] = ranked[0]
        collisions[code] = ranked
    return resolved, collisions


def _merge_ident_candidates(db, candidates_by_code):
    """For every ambiguous code, add the airport whose ICAO ident IS that
    code (small US fields have 3-char idents like 'T67') to its candidate
    pool, deduplicated by ident — so the region/nearest rule arbitrates
    between ALL airports answering to a code, whatever namespace it came
    from (iata_code, local_code, or ident)."""
    for code, cands in candidates_by_code.items():
        ident_entry = db.get(code)
        if ident_entry is not None:
            cands.append(dict(ident_entry, src="ident"))
        seen, uniq = set(), []
        for c in cands:
            key = c.get("icao") or (c["lat"], c["lon"])
            if key not in seen:
                seen.add(key)
                uniq.append(c)
        candidates_by_code[code] = uniq
    return candidates_by_code


def load_overrides():
    """Read airport_overrides.json -> {CODE: {lat, lon, icao, name}}."""
    try:
        with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, PermissionError, OSError):
        return {}


def _apply_overrides(db):
    """Overlay permanent user corrections onto the lookup dict (in place)."""
    for code, entry in load_overrides().items():
        try:
            db[code.strip().upper()] = {
                "lat": float(entry["lat"]),
                "lon": float(entry["lon"]),
                "icao": str(entry.get("icao", "")).upper(),
                "name": str(entry.get("name", "")),
                "override": True,
            }
        except (KeyError, TypeError, ValueError):
            continue
    return db


def _download_and_build():
    """Download CSV and build IATA/ICAO -> entry lookup (collision-aware)."""
    global _collisions
    print("[Airports] Downloading airport database...")
    try:
        r = requests.get(CSV_URL, timeout=30)
        r.raise_for_status()
        reader = csv.DictReader(StringIO(r.text))
        db = {}
        candidates_by_code = {}
        for row in reader:
            # Parse coordinates — stored as "lat,lon" in coordinates field
            coords_str = row.get("coordinates", "")
            if not coords_str:
                continue
            try:
                # Dataset "coordinates" field is "latitude,longitude"
                parts = coords_str.split(",")
                lat = float(parts[0].strip())
                lon = float(parts[1].strip())
            except (ValueError, AttributeError, IndexError):
                continue

            iata = row.get("iata_code", "").strip().upper()
            icao = row.get("ident", "").strip().upper()
            local = (row.get("local_code", "") or "").strip().upper()
            name = (row.get("name", "") or "").strip()
            country = (row.get("iso_country", "") or "").strip().upper()
            atype = (row.get("type", "") or "").strip()
            entry = {"lat": lat, "lon": lon, "icao": icao, "name": name,
                     "country": country, "type": atype}

            if iata and iata != "0":
                candidates_by_code.setdefault(iata, []).append(dict(entry, src="iata"))

            # US-style local codes (FAA LIDs) share the 2-4 char namespace
            # with foreign IATA codes — the dataset has ~3,000 such
            # collisions (a flight "to QSI" can mean a local US field or an
            # African IATA airport). Pool them so the region/nearest rule
            # decides, per the current-region-first preference.
            if (local and local != iata and local != icao
                    and 2 <= len(local) <= 4 and local.isalnum()):
                candidates_by_code.setdefault(local, []).append(dict(entry, src="local"))

            # Index by ICAO ident too (idents are unique in the dataset)
            if icao:
                db[icao] = entry

        home = _home_location()
        home_country = _nearest_country(db.values(), home) if home else None
        if home_country:
            print(f"[Airports] Home region for code disambiguation: {home_country}")
        _merge_ident_candidates(db, candidates_by_code)
        resolved, collisions = _resolve_iata_collisions(
            candidates_by_code, home, home_country)
        db.update(resolved)

        cache_data = {"_version": CACHE_VERSION, "airports": db,
                      "collisions": collisions}
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache_data, f)
        os.replace(tmp, CACHE_FILE)
        _collisions = collisions
        print(f"[Airports] Database built — {len(db)} entries, "
              f"{len(collisions)} IATA collisions resolved (v{CACHE_VERSION})")
        return db

    except Exception as e:
        print(f"[Airports] Download failed: {e}")
        return {}


def _load():
    """Load from cache file or download if not present."""
    global _db, _collisions, _loaded
    if _loaded:
        return

    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)

            # Versioned cache (v2+): {"_version": N, "airports": {...}}
            if isinstance(raw, dict) and raw.get("_version") == CACHE_VERSION:
                _db = _apply_overrides(raw.get("airports", {}))
                _collisions = raw.get("collisions", {})
                _loaded = True
                return
            else:
                # Stale or unversioned cache — rebuild
                version_found = raw.get("_version", "none") if isinstance(raw, dict) else "legacy"
                print(f"[Airports] Cache version mismatch (found: {version_found}, need: {CACHE_VERSION}) — rebuilding")
        except Exception as e:
            print(f"[Airports] Cache load failed: {e} — re-downloading")

    _db = _apply_overrides(_download_and_build())
    _loaded = True


def _background_refresh():
    """Download a fresh airport database in a background thread (non-blocking)."""
    global _db, _loaded, _refresh_pending, _not_found, _last_refresh_ts
    with _load_lock:
        try:
            if os.path.exists(CACHE_FILE):
                os.remove(CACHE_FILE)
            new_db = _download_and_build()
            if new_db:
                _db = _apply_overrides(new_db)
                _loaded = True
                _not_found = set()  # reset so newly-added airports can be found
        finally:
            _last_refresh_ts = time.time()
            _refresh_pending = False


# ─── Permanent user corrections (dashboard "Airport Corrections" table) ──────

def get_airport_info(code):
    """
    Full lookup for the correction UI: current entry, all known candidates
    for a colliding IATA code, and whether an override is active.
    """
    _load()
    code = (code or "").strip().upper()
    if not code:
        return {"code": "", "found": False}
    entry = _db.get(code)
    overrides = load_overrides()
    return {
        "code": code,
        "found": entry is not None,
        "entry": entry,
        "candidates": _collisions.get(code, []),
        "override": code in overrides,
    }


def set_override(code, lat, lon, icao="", name=""):
    """
    Permanently pin `code` to the given location. Persisted to
    airport_overrides.json (atomic write) and re-applied after every
    database rebuild. Returns the stored entry.
    """
    code = (code or "").strip().upper()
    if not code or not (2 <= len(code) <= 4) or not code.isalnum():
        raise ValueError("code must be 2-4 alphanumeric characters")
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("lat/lon out of range")

    entry = {"lat": lat, "lon": lon, "icao": (icao or "").strip().upper(),
             "name": (name or "").strip()}
    with _load_lock:
        overrides = load_overrides()
        overrides[code] = entry
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = OVERRIDES_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(overrides, f, indent=4)
        os.replace(tmp, OVERRIDES_FILE)
        _load()
        _db[code] = dict(entry, override=True)
        _not_found.discard(code)
    return entry


def remove_override(code):
    """Remove a permanent correction; the code reverts to the database value."""
    global _loaded
    code = (code or "").strip().upper()
    with _load_lock:
        overrides = load_overrides()
        if code not in overrides:
            return False
        del overrides[code]
        tmp = OVERRIDES_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(overrides, f, indent=4)
        os.replace(tmp, OVERRIDES_FILE)
        _loaded = False  # reload cache so the natural entry comes back
        _load()
    return True


def get_airport_coords(code):
    """
    Look up airport coordinates by IATA or ICAO code.
    Returns {"lat": float, "lon": float} or empty dict if not found.
    On a cache miss, kicks off a non-blocking background refresh so the
    display never freezes.

    Examples:
        get_airport_coords("ORD")   -> {"lat": 41.978, "lon": -87.904}
        get_airport_coords("KORD")  -> {"lat": 41.978, "lon": -87.904}
        get_airport_coords("EGLL")  -> {"lat": 51.477, "lon": -0.461}
    """
    global _refresh_pending
    _load()
    if not code:
        return {}

    code = code.strip().upper()

    # Placeholder values — never valid airport codes
    if code in ("?", "???", "N/A", "UNK", "UNKN", "ZZZZ"):
        return {}

    # Skip codes already confirmed missing (cleared after a successful refresh)
    if code in _not_found:
        return {}

    def _lookup(c):
        if c in _db:
            return _db[c]
        # Try IATA from ICAO (strip leading K for US airports)
        if len(c) == 4 and c[0] == "K":
            iata = c[1:]
            if iata in _db:
                return _db[iata]
        # Try ICAO from IATA (prepend K for US 3-letter codes)
        if len(c) == 3:
            icao = "K" + c
            if icao in _db:
                return _db[icao]
        return None

    result = _lookup(code)
    if result:
        return result

    # Miss — schedule a non-blocking background refresh if none is running.
    # Cooldown prevents a truly-unknown code from re-triggering the multi-MB
    # CSV download on every poll cycle after each refresh completes.
    if not _refresh_pending and (time.time() - _last_refresh_ts) > _REFRESH_COOLDOWN:
        _refresh_pending = True
        print(f"[Airports] '{code}' not found — scheduling background refresh...")
        t = threading.Thread(target=_background_refresh, daemon=True)
        t.start()
    else:
        _not_found.add(code)
        print(f"[Airports] '{code}' not found in database")

    return {}


def icao_to_iata(icao_code):
    """Convert 4-letter ICAO code to 3-letter IATA code using the airports database.
    Falls back to stripping leading K for US airports if not found."""
    if not icao_code or len(icao_code) != 4:
        return icao_code or "?"
    _load()
    # Search for a 3-letter key that maps to same coords as this ICAO
    icao_coords = _db.get(icao_code.upper())
    if icao_coords:
        for code, coords in _db.items():
            if (len(code) == 3 and
                abs(coords.get("lat", 0) - icao_coords.get("lat", 0)) < 0.01 and
                abs(coords.get("lon", 0) - icao_coords.get("lon", 0)) < 0.01):
                return code
    # Fall back to stripping K for US airports
    if icao_code[0] == "K":
        return icao_code[1:]
    return icao_code


def refresh():
    """Force re-download of airport database."""
    global _db, _loaded
    _loaded = False
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    _load()


if __name__ == "__main__":
    # Test
    for code in ["ORD", "KORD", "JFK", "EGLL", "HND", "LAX", "CHS"]:
        coords = get_airport_coords(code)
        print(f"{code}: {coords}")
