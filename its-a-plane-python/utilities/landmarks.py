"""
landmarks.py — Flyover context: nearest park, city, or body of water.

Ported from c0wsaysmoo/plane-tracker-rgb-pi upstream, adapted to this fork's
constraints (pure-local lookups only — upstream's Nominatim reverse geocoding
and the NPS API download are deliberately NOT ported), and carrying AJ
Plotkin's ocean-coverage and name-truncation fixes (ea35c32).

Lookup chain for a tracked aircraft position:
  1. US National Park within PARK_RADIUS_KM  -> park name ("Grand Canyon")
  2. Nearest city within CITIES_MAX_KM       -> city name (indexed lookup
     via utilities.cities — see its render-thread performance notes)
  3. Named sea/gulf from bounding boxes      -> "Gulf of Mexico"
  4. If very far from any city, ocean basin  -> "North Pacific" (total —
     never None over open water)
  5. Otherwise the nearest city anyway — this fork always showed the
     nearest city no matter how far, and remote land (Sahara, outback)
     is better served by a distant town than by a wrong ocean name.

PERFORMANCE: get_nearest_landmark() runs on the RENDER thread (called from
scenes/trackedstats.py under its 0.01-degree movement cache). Everything here
is O(len(_PARKS)) plus one indexed city lookup — no full-database scans.
"""

from utilities.cities import get_nearest_city, _haversine_km

PARK_RADIUS_KM = 30     # a park counts as "overhead" within this distance
CITIES_MAX_KM = 200     # beyond this, prefer a water name over a city
REMOTE_KM = 500         # beyond this, no box needed — assume open water
MAX_NAME_LEN = 24

_STRIP_SUFFIXES = [
    "National Monument and Preserve", "National Monument & Preserve",
    "National Park and Preserve", "National Park & Preserve",
    "National Recreation Area", "National Historical Park",
    "National Historic Site", "National Memorial", "National Monument",
    "National Seashore", "National Lakeshore", "National Parkway",
    "National Reserve", "National Forest", "National Refuge",
    "National Park", "State Historic Park", "State Recreation Area",
    "State Forest", "State Park", "Provincial Park", "Regional Park",
    "Country Park", "Nature Reserve", "Wildlife Refuge",
    "Wilderness Area", "Historic Site", "Heritage Site",
]

# Characters that must not be left dangling at a truncation point.
_TRAILING_JUNK = " -,;:./("


def _truncate_name(name):
    """Trim to MAX_NAME_LEN ending on a whole word.

    A plain name[:MAX_NAME_LEN] slice cuts mid-word: measured against the
    GeoNames cities5000 set that is 518 of 69,629 names, rendering as
    "Dubai International Fina" and "Notre-Dame-de-l'Ile-Perr". Prefer the last
    space or hyphen inside the limit; fall back to the hard slice only when
    that would leave too little to identify the place.
    """
    if len(name) <= MAX_NAME_LEN:
        return name
    head = name[:MAX_NAME_LEN]
    cut = max(head.rfind(" "), head.rfind("-"))
    if cut >= MAX_NAME_LEN // 2:
        out = head[:cut].rstrip(_TRAILING_JUNK)
    else:
        out = head.rstrip(_TRAILING_JUNK)
    return out or head          # never return an empty name


def _clean_name(name):
    """Strip park-type suffixes, then truncate rather than discard.

    Upstream returned None for any name over MAX_NAME_LEN, which threw away a
    park the aircraft is actually over just because its name is long — e.g.
    "Washington-Rochambeau Revolutionary Route National Historic Trail" is
    still 65 chars after suffix stripping and was never displayable.
    """
    if not name:
        return None
    stripped = name.strip()
    for suffix in _STRIP_SUFFIXES:
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)].rstrip(" ,–-")
            break
    return _truncate_name(stripped) if stripped else None


# ---------------------------------------------------------------------------
# Ocean/sea detection — coordinate based
# Rough bounding boxes (lat_min, lat_max, lon_min, lon_max), most-specific
# first. Carries ajplotkin's corrections: Caribbean west edge, Bay of Biscay,
# English Channel, and the Caspian.
# ---------------------------------------------------------------------------

# Seas and gulfs — specific enough that a match while >CITIES_MAX_KM from any
# city is trustworthy on its own.
_SEA_REGIONS = [
    # West edge -84, not -87: at -85..-87 this box claimed the PACIFIC coast of
    # Costa Rica and Nicaragua. Yucatan waters west of -84 fall to Gulf of Mexico.
    ("Caribbean Sea",       (8,  26, -84, -59)),
    ("Gulf of Mexico",      (18, 31, -98, -80)),
    # Biscay and the Channel must precede the Mediterranean, whose -6..37 /
    # 30..47 rectangle otherwise swallows the whole western approach to France.
    ("Bay of Biscay",       (43, 49, -12,  -1)),
    ("English Channel",     (49, 51,  -6,   2)),
    ("Mediterranean Sea",   (30, 47,  -6,  37)),
    ("North Sea",           (51, 62,  -4,  13)),
    ("Baltic Sea",          (53, 66,  10,  30)),
    ("Black Sea",           (41, 47,  28,  42)),
    # The Caspian is the one large lake that reaches this code: it is
    # international water far from any cities5000 shore gap issues, and it had
    # no box at all upstream. Superior, Baikal, Victoria and Great Bear are all
    # within CITIES_MAX_KM of shore cities and stop at step 2.
    ("Caspian Sea",         (36, 47,  46,  55)),
    ("Red Sea",             (12, 30,  32,  44)),
    ("Persian Gulf",        (22, 30,  47,  57)),
    ("Arabian Sea",         (5,  26,  52,  78)),
    ("Bay of Bengal",       (5,  23,  78, 100)),
    ("South China Sea",     (0,  25, 100, 122)),
    ("East China Sea",      (24, 34, 120, 132)),
    ("Sea of Japan",        (34, 52, 128, 142)),
    ("Bering Sea",          (52, 66, 163, 180)),
    ("Gulf of Alaska",      (52, 62, -152, -130)),
    ("Hudson Bay",          (51, 66, -95, -65)),
    ("Coral Sea",           (-25, -8, 142, 160)),
    ("Tasman Sea",          (-48, -28, 150, 175)),
    ("Norwegian Sea",       (62, 78, -15,  30)),
    ("Barents Sea",         (68, 82,  15,  60)),
    ("Labrador Sea",        (50, 68, -65, -42)),
]

# Ocean basins — broad rectangles that unavoidably overlap large land areas
# (Kansas sits inside the "North Pacific" box, New York inside "North
# Atlantic"). Only consulted once the position is effectively known to be
# over water; checked after the seas.
_BASIN_REGIONS = [
    ("Arctic Ocean",        (70, 90, -180, 180)),
    ("Southern Ocean",      (-90, -55, -180, 180)),
    ("North Atlantic",      (0,  66, -80,   0)),
    ("South Atlantic",      (-55,  0, -70,  20)),
    ("North Pacific",       (0,  66, -180, -80)),
    ("South Pacific",       (-55,  0, -180, -100)),
    ("Indian Ocean",        (-55, 30,  20, 120)),
]

_OCEAN_REGIONS = _SEA_REGIONS + _BASIN_REGIONS


def _match_box(regions, lat, lon):
    for name, (lat_min, lat_max, lon_min, lon_max) in regions:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return name
    return None


def _get_sea_name(lat, lon):
    """Specific sea/gulf boxes only — safe to trust over ambiguous positions."""
    return _match_box(_SEA_REGIONS, lat, lon)


def _get_ocean_name(lat, lon):
    """Named sea/gulf/ocean if any box matches, else None. Deliberately
    PARTIAL — the Sahara must return None from here."""
    return _match_box(_OCEAN_REGIONS, lat, lon)


def _ocean_basin(lat, lon):
    """Coarse basin name for a position already known to be over water.

    TOTAL by construction. The boxes above leave real gaps a rectangle list
    cannot close: sweeping the globe at 5 degrees found 115 open-water probes
    with no name at all, including the whole SE Pacific off Chile and the North
    Pacific west of the dateline (the Tokyo-Hawaii tracks), because the
    "North Pacific" box only runs -180..-80. Reaching the fallback with no name
    means no landmark is shown at all.

    The Atlantic/Pacific split cannot be a single meridian -- the Americas are in
    the way -- so it follows the land: Panama (~-80) in the north, Cape Horn
    (~-70) in the south. The Indian Ocean likewise reaches further east below the
    equator, around Australia's south coast (~147).
    """
    if lat >= 66:
        return "Arctic Ocean"
    if lat <= -55:
        return "Southern Ocean"
    if 20 <= lon < (120 if lat >= 0 else 147):
        return "Indian Ocean"
    if (-80 if lat >= 0 else -70) <= lon < 20:
        return "North Atlantic" if lat >= 0 else "South Atlantic"
    return "North Pacific" if lat >= 0 else "South Pacific"


def _water_name(lat, lon):
    """Named sea if a box matches, else the basin. Never None."""
    return _get_ocean_name(lat, lon) or _ocean_basin(lat, lon)


# ---------------------------------------------------------------------------
# US National Parks — static embedded data.
#
# Upstream downloads ~470 NPS units through the NPS API (needs NPS_API_KEY);
# this fork is pure-local, so the 63 designated National Parks are embedded
# directly. 63 haversines per lookup is microseconds — no index needed.
# Names carry their suffix so _clean_name renders them as upstream did
# ("Grand Canyon National Park" -> "Grand Canyon").
# ---------------------------------------------------------------------------

_PARKS = [
    ("Acadia National Park", 44.35, -68.21),
    ("National Park of American Samoa", -14.25, -170.68),
    ("Arches National Park", 38.68, -109.57),
    ("Badlands National Park", 43.75, -102.50),
    ("Big Bend National Park", 29.25, -103.25),
    ("Biscayne National Park", 25.65, -80.08),
    ("Black Canyon of the Gunnison National Park", 38.57, -107.72),
    ("Bryce Canyon National Park", 37.57, -112.18),
    ("Canyonlands National Park", 38.20, -109.93),
    ("Capitol Reef National Park", 38.20, -111.17),
    ("Carlsbad Caverns National Park", 32.17, -104.44),
    ("Channel Islands National Park", 34.01, -119.42),
    ("Congaree National Park", 33.78, -80.78),
    ("Crater Lake National Park", 42.94, -122.10),
    ("Cuyahoga Valley National Park", 41.24, -81.55),
    ("Death Valley National Park", 36.24, -116.82),
    ("Denali National Park and Preserve", 63.33, -150.50),
    ("Dry Tortugas National Park", 24.63, -82.87),
    ("Everglades National Park", 25.32, -80.93),
    ("Gates of the Arctic National Park and Preserve", 67.78, -153.30),
    ("Gateway Arch National Park", 38.63, -90.19),
    ("Glacier National Park", 48.70, -113.80),
    ("Glacier Bay National Park and Preserve", 58.50, -137.00),
    ("Grand Canyon National Park", 36.06, -112.14),
    ("Grand Teton National Park", 43.73, -110.80),
    ("Great Basin National Park", 38.98, -114.30),
    ("Great Sand Dunes National Park and Preserve", 37.73, -105.51),
    ("Great Smoky Mountains National Park", 35.61, -83.48),
    ("Guadalupe Mountains National Park", 31.92, -104.87),
    ("Haleakala National Park", 20.72, -156.17),
    ("Hawaii Volcanoes National Park", 19.38, -155.20),
    ("Hot Springs National Park", 34.51, -93.05),
    ("Indiana Dunes National Park", 41.65, -87.05),
    ("Isle Royale National Park", 48.00, -88.55),
    ("Joshua Tree National Park", 33.87, -115.90),
    ("Katmai National Park and Preserve", 58.50, -155.00),
    ("Kenai Fjords National Park", 59.92, -149.65),
    ("Kings Canyon National Park", 36.89, -118.55),
    ("Kobuk Valley National Park", 67.33, -159.12),
    ("Lake Clark National Park and Preserve", 60.97, -153.42),
    ("Lassen Volcanic National Park", 40.49, -121.51),
    ("Mammoth Cave National Park", 37.18, -86.10),
    ("Mesa Verde National Park", 37.18, -108.49),
    ("Mount Rainier National Park", 46.85, -121.75),
    ("New River Gorge National Park", 38.07, -81.08),
    ("North Cascades National Park", 48.70, -121.20),
    ("Olympic National Park", 47.97, -123.50),
    ("Petrified Forest National Park", 35.07, -109.78),
    ("Pinnacles National Park", 36.48, -121.16),
    ("Redwood National Park", 41.30, -124.00),
    ("Rocky Mountain National Park", 40.40, -105.58),
    ("Saguaro National Park", 32.25, -110.50),
    ("Sequoia National Park", 36.43, -118.68),
    ("Shenandoah National Park", 38.53, -78.35),
    ("Theodore Roosevelt National Park", 46.97, -103.45),
    ("Virgin Islands National Park", 18.33, -64.73),
    ("Voyageurs National Park", 48.50, -92.88),
    ("White Sands National Park", 32.78, -106.17),
    ("Wind Cave National Park", 43.57, -103.48),
    ("Wrangell-St. Elias National Park and Preserve", 61.00, -142.00),
    ("Yellowstone National Park", 44.60, -110.50),
    ("Yosemite National Park", 37.83, -119.50),
    ("Zion National Park", 37.30, -113.05),
]


def get_nearby_parks(latitude, longitude, radius_km):
    """All parks within radius_km, nearest first.
    Each entry is {"name": str, "distance_km": float} (raw name, not cleaned)."""
    results = []
    for name, plat, plon in _PARKS:
        dist = _haversine_km(latitude, longitude, plat, plon)
        if dist <= radius_km:
            results.append({"name": name, "distance_km": dist})
    results.sort(key=lambda x: x["distance_km"])
    return results


def _nearest_park(lat, lon):
    for result in get_nearby_parks(lat, lon, PARK_RADIUS_KM):
        name = _clean_name(result["name"])
        if name:
            return name, result["distance_km"]
    return None, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_nearest_landmark(latitude, longitude):
    """
    Flyover context for a position. Returns {"name": str, "kind": str} with
    kind in ("park", "city", "water"), or None only if the cities database
    is unavailable AND no water box matches (effectively never over water).
    """
    park_name, park_dist = _nearest_park(latitude, longitude)
    if park_name:
        return {"name": park_name, "kind": "park", "distance_km": park_dist}

    city = get_nearest_city(latitude, longitude)
    if city and city["distance_km"] <= CITIES_MAX_KM:
        return {"name": _truncate_name(city["name"]),
                "kind": "city", "distance_km": city["distance_km"]}

    # Far from every city. Over water this is where the ocean name belongs;
    # a SPECIFIC sea box is trusted outright — the >CITIES_MAX_KM gate plays
    # the role of upstream's "Nominatim found no country" gate. The broad
    # basin rectangles are NOT trusted here: they overlap real land.
    sea = _get_sea_name(latitude, longitude)
    if sea:
        return {"name": sea, "kind": "water", "distance_km": None}

    # No sea box matched. Upstream resolved this with Nominatim's country code
    # (Sahara -> "Algeria"); without a geocoder, distance discriminates:
    # remote LAND is still usually within REMOTE_KM of some cities5000 town
    # (desert oases, outback stations), while genuine open-ocean gaps — the
    # SE Pacific off Chile, the North Pacific west of the dateline — are not.
    if city and city["distance_km"] <= REMOTE_KM:
        return {"name": _truncate_name(city["name"]),
                "kind": "city", "distance_km": city["distance_km"]}

    if city:
        return {"name": _ocean_basin(latitude, longitude),
                "kind": "water", "distance_km": None}

    # Cities database unavailable (get_nearest_city returned None): without
    # city distances we cannot tell remote land from open water, so only the
    # explicit sea boxes were trustworthy — and none matched.
    return None
