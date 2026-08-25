"""
Spatial index for utilities/cities.get_nearest_city().

get_nearest_city() runs on the RENDER THREAD: scenes/trackedstats.py calls it
whenever the tracked aircraft moves past _CITY_CACHE_THRESHOLD (0.01 deg, ~1km),
which at cruise is roughly every 4 seconds. A haversine over the whole ~69,000-city
database measured 437ms on a Pi 3A+ against a 100ms frame budget, so each lookup was
a visible scroll freeze.

The index must be BOTH faster and exactly equivalent to the old full scan — these
tests pin the equivalence, because the speed is worthless if the answer moves.
"""

import random

import pytest

from utilities import cities as C


def _exhaustive(db, lat, lon):
    """The pre-index implementation, kept as the oracle."""
    best_name, best_dist = None, float("inf")
    for name, clat, clon in db:
        d = C._haversine_km(lat, lon, clat, clon)
        if d < best_dist:
            best_dist, best_name = d, name
    return (best_name, round(best_dist, 6)) if best_name else None


def _with_db(db):
    C._db, C._loaded, C._grid, C._grid_src = db, True, None, None


@pytest.fixture
def db():
    saved = (C._db, C._loaded, C._grid, C._grid_src)
    random.seed(1234)
    d = [[f"city{i}", random.uniform(-89, 89), random.uniform(-180, 180)]
         for i in range(3000)]
    d += [["anti_a", 0.5, 179.6], ["anti_b", 0.5, -179.6]]
    _with_db(d)
    yield d
    C._db, C._loaded, C._grid, C._grid_src = saved


def test_matches_the_exhaustive_scan(db):
    random.seed(99)
    probes = [(47.68, -118.87), (0, 179.9), (0, -179.9), (45, -179.5),
              (89.5, 0), (-89.5, 0), (0, 0)]
    probes += [(random.uniform(-89, 89), random.uniform(-180, 180))
               for _ in range(150)]
    for lat, lon in probes:
        got = C.get_nearest_city(lat, lon)
        want = _exhaustive(db, lat, lon)
        got_t = (got["name"], round(got["distance_km"], 6)) if got else None
        assert got_t == want, f"index disagreed at {lat},{lon}"


def test_stopping_bound_is_a_true_great_circle_lower_bound():
    """`ring * 111 * cos(lat)` measures along a parallel, which OVERSTATES how far
    the meridian really is (a great circle cuts poleward). Using it stops the search
    too early and returns the wrong city."""
    saved = (C._db, C._loaded, C._grid, C._grid_src)
    try:
        _with_db([["decoy", 41.29, 0.999], ["hidden", 60.999, 42.001]])
        got = C.get_nearest_city(60.999, 0.999)
        assert got["name"] == "hidden", (
            f"stopped early and returned {got['name']} — bound is not a lower bound")
    finally:
        C._db, C._loaded, C._grid, C._grid_src = saved


def test_city_at_exactly_positive_180_is_reachable():
    saved = (C._db, C._loaded, C._grid, C._grid_src)
    try:
        _with_db([["e180", 0.0, 180.0], ["far", 0.0, 100.0]])
        assert C.get_nearest_city(0.0, 179.9)["name"] == "e180"
    finally:
        C._db, C._loaded, C._grid, C._grid_src = saved


def test_index_rebuilds_when_the_database_is_replaced():
    saved = (C._db, C._loaded, C._grid, C._grid_src)
    try:
        _with_db([["alpha", 10.0, 10.0]])
        assert C.get_nearest_city(10.0, 10.01)["name"] == "alpha"
        C._db = [["beta", 10.0, 10.0]]
        assert C.get_nearest_city(10.0, 10.01)["name"] == "beta"
    finally:
        C._db, C._loaded, C._grid, C._grid_src = saved


def test_it_actually_prunes(db):
    """If a change silently reverts to scanning everything the answers stay right
    and the render-thread stall comes back — so assert the work done, not just the
    result."""
    calls = {"n": 0}
    real = C._haversine_km

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    C._haversine_km = counting
    try:
        C._grid = None
        C._grid_src = None
        C.get_nearest_city(47.68, -118.87)
    finally:
        C._haversine_km = real
    assert calls["n"] < len(db) / 4, (
        f"examined {calls['n']} of {len(db)} cities — the index is not pruning")


def test_empty_database_returns_none():
    saved = (C._db, C._loaded, C._grid, C._grid_src)
    try:
        _with_db([])
        assert C.get_nearest_city(40.0, -73.0) is None
    finally:
        C._db, C._loaded, C._grid, C._grid_src = saved
