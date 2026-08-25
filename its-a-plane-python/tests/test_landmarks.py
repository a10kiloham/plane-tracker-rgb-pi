"""
Unit tests for utilities/landmarks.py — flyover context (parks / cities / oceans).

Covers:
  - known coordinates -> expected park / ocean names
  - the ocean-coverage gaps ajplotkin's fix (ea35c32) closes: the total
    _ocean_basin(), the Caribbean/Pacific box correction, Bay of Biscay vs
    Mediterranean, and the Caspian
  - name truncation at word boundaries instead of silently dropping long names
  - the get_nearest_landmark() chain against a stubbed cities database
    (no network, no cities.json download)
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utilities import cities as C
from utilities import landmarks as L


@pytest.fixture
def stub_cities():
    """Install a tiny in-memory cities database; restore afterwards."""
    saved = (C._db, C._loaded, C._grid, C._grid_src)

    def install(db):
        C._db, C._loaded, C._grid, C._grid_src = db, True, None, None

    yield install
    C._db, C._loaded, C._grid, C._grid_src = saved


# ═══════════════════════════════════════════════════════════════════════════════
# Parks
# ═══════════════════════════════════════════════════════════════════════════════

class TestParks:
    def test_grand_canyon_overhead(self):
        name, dist = L._nearest_park(36.06, -112.14)
        assert name == "Grand Canyon"
        assert dist == pytest.approx(0.0, abs=1e-6)

    def test_yellowstone_nearby(self):
        # ~20km from the reference point, inside PARK_RADIUS_KM
        name, dist = L._nearest_park(44.45, -110.65)
        assert name == "Yellowstone"
        assert dist < L.PARK_RADIUS_KM

    def test_suffix_and_preserve_forms_are_stripped(self):
        name, _ = L._nearest_park(63.33, -150.50)
        assert name == "Denali"

    def test_no_park_far_away(self):
        # Middle of Kansas — no National Park within 30km
        name, dist = L._nearest_park(38.5, -98.0)
        assert name is None and dist is None

    def test_get_nearby_parks_sorted_and_bounded(self):
        results = L.get_nearby_parks(36.06, -112.14, 30)
        assert results and results[0]["name"] == "Grand Canyon National Park"
        dists = [r["distance_km"] for r in results]
        assert dists == sorted(dists)
        assert all(d <= 30 for d in dists)


# ═══════════════════════════════════════════════════════════════════════════════
# Ocean boxes and the total basin fallback (ajplotkin ea35c32)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOceanNames:
    def test_known_seas(self):
        assert L._get_ocean_name(25.5, -90.0) == "Gulf of Mexico"
        assert L._get_ocean_name(36.0, 18.0) == "Mediterranean Sea"
        assert L._get_ocean_name(56.0, 3.0) == "North Sea"

    def test_sahara_returns_none_from_partial_lookup(self):
        # _get_ocean_name is deliberately PARTIAL — land off-box must be None
        assert L._get_ocean_name(25.0, 10.0) is None

    def test_caribbean_box_no_longer_claims_pacific_coast(self):
        # (9, -86) is the PACIFIC side of Costa Rica; the old -87 west edge
        # labelled it "Caribbean Sea"
        assert L._get_ocean_name(9.0, -86.0) != "Caribbean Sea"
        assert L._get_ocean_name(15.0, -75.0) == "Caribbean Sea"

    def test_bay_of_biscay_not_mediterranean(self):
        # The Mediterranean box (-6..37 / 30..47) used to swallow Biscay
        assert L._get_ocean_name(45.5, -4.0) == "Bay of Biscay"
        assert L._get_ocean_name(50.0, -1.0) == "English Channel"

    def test_caspian_sea_has_a_box(self):
        assert L._get_ocean_name(42.0, 50.0) == "Caspian Sea"

    def test_basin_closes_the_se_pacific_gap(self):
        # SE Pacific off Chile: the "South Pacific" box stops at -100 and the
        # "South Atlantic" one starts at -70, so -100..-70 was a hole. No box
        # matches, and the basin must still name it.
        assert L._get_ocean_name(-40.0, -90.0) is None
        assert L._water_name(-40.0, -90.0) == "South Pacific"

    def test_basin_closes_the_west_of_dateline_gap(self):
        # Tokyo-Hawaii tracks: the North Pacific box only ran -180..-80
        assert L._water_name(35.0, 170.0) == "North Pacific"

    def test_basin_is_total(self):
        # Sweep the globe: _water_name never returns None
        for lat in range(-90, 91, 5):
            for lon in range(-180, 181, 5):
                assert L._water_name(lat, lon) is not None

    def test_basin_atlantic_pacific_split_follows_the_americas(self):
        # Panama (~-80) in the north, Cape Horn (~-70) in the south
        assert L._ocean_basin(10.0, -75.0) == "North Atlantic"
        assert L._ocean_basin(-40.0, -75.0) == "South Pacific"


# ═══════════════════════════════════════════════════════════════════════════════
# Name truncation (ajplotkin ea35c32)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNameTruncation:
    def test_short_names_untouched(self):
        assert L._truncate_name("Yellowstone") == "Yellowstone"

    def test_truncates_at_word_boundary(self):
        # A plain slice would render "Dubai International Fina"
        assert L._truncate_name("Dubai International Financial Centre") == \
            "Dubai International"

    def test_truncates_at_hyphen_boundary(self):
        out = L._truncate_name("Notre-Dame-de-l'Ile-Perrot")
        assert len(out) <= L.MAX_NAME_LEN
        assert out == "Notre-Dame-de-l'Ile"

    def test_never_empty(self):
        out = L._truncate_name("x" * 60)
        assert out and len(out) <= L.MAX_NAME_LEN

    def test_long_park_name_is_truncated_not_dropped(self):
        # Upstream's _clean_name returned None here, so a park the aircraft was
        # actually over could never be displayed
        name = L._clean_name(
            "Washington-Rochambeau Revolutionary Route National Historic Trail")
        assert name is not None
        assert len(name) <= L.MAX_NAME_LEN
        assert name.startswith("Washington-Rochambeau")

    def test_clean_name_strips_park_suffix(self):
        assert L._clean_name("Grand Canyon National Park") == "Grand Canyon"
        assert L._clean_name("") is None
        assert L._clean_name(None) is None


# ═══════════════════════════════════════════════════════════════════════════════
# get_nearest_landmark() chain
# ═══════════════════════════════════════════════════════════════════════════════

class TestLandmarkChain:
    def test_park_wins_over_city(self, stub_cities):
        stub_cities([["Tusayan", 35.97, -112.13]])   # 10km from the rim
        got = L.get_nearest_landmark(36.06, -112.14)
        assert got == {"name": "Grand Canyon", "kind": "park",
                       "distance_km": pytest.approx(0.0, abs=1e-6)}

    def test_nearby_city_when_no_park(self, stub_cities):
        stub_cities([["Wichita", 37.69, -97.34]])
        got = L.get_nearest_landmark(37.75, -97.30)
        assert got["kind"] == "city"
        assert got["name"] == "Wichita"

    def test_open_ocean_names_the_water_not_a_distant_city(self, stub_cities):
        stub_cities([["Hilo", 19.73, -155.09]])
        got = L.get_nearest_landmark(35.0, 170.0)    # mid North Pacific
        assert got == {"name": "North Pacific", "kind": "water",
                       "distance_km": None}

    def test_named_sea_box_beyond_city_range(self, stub_cities):
        stub_cities([["New Orleans", 29.95, -90.07]])
        got = L.get_nearest_landmark(25.5, -90.0)    # central Gulf, ~495km out
        assert got == {"name": "Gulf of Mexico", "kind": "water",
                       "distance_km": None}

    def test_remote_land_prefers_a_far_city_over_a_wrong_ocean(self, stub_cities):
        # Deep Sahara: no box matches, and the nearest town is 200-500km away.
        # Upstream resolved this via Nominatim's country code; here the far
        # city must win over the (wrong) basin name.
        stub_cities([["Tamanrasset", 22.79, 5.53]])
        got = L.get_nearest_landmark(25.0, 8.0)      # ~350km away
        assert got["kind"] == "city"
        assert got["name"] == "Tamanrasset"

    def test_city_name_is_truncated(self, stub_cities):
        stub_cities([["Dubai International Financial Centre", 25.21, 55.28]])
        got = L.get_nearest_landmark(25.21, 55.28)
        assert got["name"] == "Dubai International"

    def test_empty_cities_db_over_land_returns_none(self, stub_cities):
        stub_cities([])
        assert L.get_nearest_landmark(38.5, -98.0) is None

    def test_empty_cities_db_over_boxed_water_still_names_it(self, stub_cities):
        stub_cities([])
        got = L.get_nearest_landmark(25.5, -90.0)
        assert got["name"] == "Gulf of Mexico"
