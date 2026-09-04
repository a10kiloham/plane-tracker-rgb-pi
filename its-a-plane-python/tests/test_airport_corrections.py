"""Tests for airport IATA disambiguation, permanent overrides, and the
unknown-route display filters."""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Match the env defaults other test modules set before config/overhead import
os.environ.setdefault("DISTANCE_UNITS", "imperial")
os.environ.setdefault("PLANE_TRACKER_DATA_DIR", tempfile.mkdtemp())

from utilities import airports as ap
from utilities.overhead import (
    DISPLAY_SETTINGS_DEFAULTS,
    should_ignore_unknown,
)


# ─── Collision resolution ────────────────────────────────────────────────────

US_QSI = {"lat": 40.0, "lon": -100.0, "icao": "KQSI", "name": "US Field", "country": "US"}
MZ_QSI = {"lat": -20.0, "lon": 34.0, "icao": "FQSI", "name": "Mozambique Field", "country": "MZ"}
HOME_US = (41.0, -87.0)  # Chicago-ish


def test_single_candidate_passes_through():
    resolved, collisions = ap._resolve_iata_collisions({"ORD": [US_QSI]}, HOME_US)
    assert resolved["ORD"] == US_QSI
    assert collisions == {}


def test_home_country_wins_over_order():
    # African airport listed first — the US one must still win for a US home
    resolved, collisions = ap._resolve_iata_collisions(
        {"QSI": [MZ_QSI, US_QSI]}, HOME_US, home_country="US")
    assert resolved["QSI"]["icao"] == "KQSI"
    assert len(collisions["QSI"]) == 2


def test_home_country_beats_distance():
    # A US small field with a NON-K ident must beat a nearer foreign airport
    # (this is why the rule matches iso_country, not the ICAO first letter)
    us_small = {"lat": 60.0, "lon": -150.0, "icao": "2AK6", "name": "Hog River",
                "country": "US"}
    mx_near = {"lat": 25.7, "lon": -100.3, "icao": "MX-0625", "name": "Monterrey H",
               "country": "MX"}
    resolved, _ = ap._resolve_iata_collisions(
        {"HGZ": [mx_near, us_small]}, HOME_US, home_country="US")
    assert resolved["HGZ"]["icao"] == "2AK6"


def test_nearest_wins_when_no_country_match():
    far = {"lat": -30.0, "lon": 150.0, "icao": "YSSY", "name": "Far", "country": "AU"}
    near = {"lat": 45.0, "lon": -75.0, "icao": "CYOW", "name": "Near", "country": "CA"}
    resolved, _ = ap._resolve_iata_collisions({"XXX": [far, near]}, HOME_US,
                                              home_country="US")
    assert resolved["XXX"]["icao"] == "CYOW"


def test_home_country_derived_from_nearest_airport():
    # Home near London: nearest candidate is GB, so GB beats US even though
    # no home_country was passed explicitly
    uk = {"lat": 51.47, "lon": -0.46, "icao": "EGLL", "name": "Heathrow", "country": "GB"}
    us = {"lat": 33.94, "lon": -118.4, "icao": "KLAX", "name": "LAX", "country": "US"}
    resolved, _ = ap._resolve_iata_collisions({"AAA": [us, uk]}, (52.0, 0.0))
    assert resolved["AAA"]["icao"] == "EGLL"


def test_no_home_prefers_real_icao():
    resolved, _ = ap._resolve_iata_collisions({"QSI": [MZ_QSI, US_QSI]}, None)
    assert resolved["QSI"]["icao"] in ("FQSI", "KQSI")  # deterministic, no crash


def test_major_airport_iata_beats_home_country_strip():
    # "AMS" must be Schiphol even for a US home with a nearer strip whose
    # local code is AMS
    schiphol = {"lat": 52.31, "lon": 4.76, "icao": "EHAM", "name": "Schiphol",
                "country": "NL", "type": "large_airport", "src": "iata"}
    strip = {"lat": 40.0, "lon": -95.0, "icao": "MX-1202", "name": "Strip",
             "country": "US", "type": "small_airport", "src": "local"}
    resolved, _ = ap._resolve_iata_collisions(
        {"AMS": [strip, schiphol]}, HOME_US, home_country="US")
    assert resolved["AMS"]["icao"] == "EHAM"


def test_home_country_beats_foreign_medium():
    # But below large_airport, the home-country rule still rules
    medium = {"lat": 5.0, "lon": -72.0, "icao": "SKCU", "name": "Cusiana",
              "country": "CO", "type": "medium_airport", "src": "iata"}
    strip = {"lat": 31.8, "lon": -107.6, "icao": "0NM0", "name": "Columbus",
             "country": "US", "type": "small_airport", "src": "local"}
    resolved, _ = ap._resolve_iata_collisions(
        {"CUS": [medium, strip]}, HOME_US, home_country="US")
    assert resolved["CUS"]["icao"] == "0NM0"


def test_merge_ident_candidates_dedupes():
    ident_entry = {"lat": 1.0, "lon": 2.0, "icao": "T67", "name": "Own Field",
                   "country": "US"}
    other = {"lat": 3.0, "lon": 4.0, "icao": "KAAA", "name": "Other", "country": "US"}
    db = {"T67": ident_entry}
    cands = {"T67": [other, dict(ident_entry)]}
    ap._merge_ident_candidates(db, cands)
    icaos = [c["icao"] for c in cands["T67"]]
    assert icaos.count("T67") == 1 and "KAAA" in icaos


# ─── Overrides ───────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_airports(tmp_path, monkeypatch):
    """Point the airports module at temp files with a known v3 cache."""
    cache = tmp_path / "airports.json"
    cache.write_text(json.dumps({
        "_version": ap.CACHE_VERSION,
        "airports": {"QSI": dict(MZ_QSI), "KQSI": dict(US_QSI)},
        "collisions": {"QSI": [MZ_QSI, US_QSI]},
    }))
    monkeypatch.setattr(ap, "CACHE_FILE", str(cache))
    monkeypatch.setattr(ap, "OVERRIDES_FILE", str(tmp_path / "airport_overrides.json"))
    monkeypatch.setattr(ap, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ap, "_loaded", False)
    monkeypatch.setattr(ap, "_db", {})
    monkeypatch.setattr(ap, "_collisions", {})
    monkeypatch.setattr(ap, "_not_found", set())
    yield tmp_path
    ap._loaded = False
    ap._db = {}
    ap._collisions = {}


def test_override_roundtrip(isolated_airports):
    # Before: DB resolves QSI to the Mozambique entry
    assert ap.get_airport_info("QSI")["entry"]["icao"] == "FQSI"

    ap.set_override("QSI", US_QSI["lat"], US_QSI["lon"], icao="KQSI", name="US Field")
    info = ap.get_airport_info("QSI")
    assert info["override"] is True
    assert info["entry"]["icao"] == "KQSI"
    assert ap.get_airport_coords("QSI")["lat"] == US_QSI["lat"]

    # Survives a reload (simulating restart / DB rebuild path)
    ap._loaded = False
    assert ap.get_airport_coords("QSI")["lat"] == US_QSI["lat"]

    # Remove -> reverts to database value
    assert ap.remove_override("QSI") is True
    assert ap.get_airport_info("QSI")["entry"]["icao"] == "FQSI"
    assert ap.remove_override("QSI") is False


def test_override_validation(isolated_airports):
    with pytest.raises(ValueError):
        ap.set_override("", 1, 2)
    with pytest.raises(ValueError):
        ap.set_override("TOOLONG", 1, 2)
    with pytest.raises(ValueError):
        ap.set_override("QSI", 95.0, 0.0)  # lat out of range


def test_candidates_exposed_for_ui(isolated_airports):
    info = ap.get_airport_info("QSI")
    assert len(info["candidates"]) == 2
    icaos = {c["icao"] for c in info["candidates"]}
    assert icaos == {"FQSI", "KQSI"}


# ─── Unknown-route display filters ───────────────────────────────────────────

def _settings(single=False, double=False):
    return {"ignore_single_unknown": single, "ignore_double_unknown": double}


def test_defaults_hide_nothing():
    for o, d in [("", ""), ("LAX", ""), ("", "LAX"), ("LAX", "JFK")]:
        assert should_ignore_unknown(o, d, dict(DISPLAY_SETTINGS_DEFAULTS)) is False


def test_double_unknown_filter():
    s = _settings(double=True)
    assert should_ignore_unknown("", "", s) is True
    assert should_ignore_unknown("LAX", "", s) is False
    assert should_ignore_unknown("", "LAX", s) is False
    assert should_ignore_unknown("LAX", "JFK", s) is False


def test_single_unknown_filter():
    s = _settings(single=True)
    assert should_ignore_unknown("LAX", "", s) is True
    assert should_ignore_unknown("", "LAX", s) is True
    assert should_ignore_unknown("", "", s) is False  # double is its own toggle
    assert should_ignore_unknown("LAX", "JFK", s) is False


def test_both_filters():
    s = _settings(single=True, double=True)
    assert should_ignore_unknown("", "", s) is True
    assert should_ignore_unknown("LAX", "", s) is True
    assert should_ignore_unknown("LAX", "JFK", s) is False


def test_display_settings_persistence(tmp_path, monkeypatch):
    from utilities import overhead as oh
    path = str(tmp_path / "display_settings.json")
    monkeypatch.setattr(oh, "DISPLAY_SETTINGS_FILE", path)
    monkeypatch.setattr(oh, "_display_settings_cache", {"mtime": None, "settings": {}})

    # Missing file -> defaults
    assert oh.load_display_settings() == DISPLAY_SETTINGS_DEFAULTS

    saved = oh.save_display_settings({"ignore_double_unknown": True, "junk_key": True})
    assert saved == {"ignore_single_unknown": False, "ignore_double_unknown": True}
    assert "junk_key" not in saved
    assert oh.load_display_settings()["ignore_double_unknown"] is True

    # Corrupt file -> defaults (not a crash)
    with open(path, "w") as f:
        f.write("{not json")
    oh._display_settings_cache["mtime"] = None
    assert oh.load_display_settings() == DISPLAY_SETTINGS_DEFAULTS
