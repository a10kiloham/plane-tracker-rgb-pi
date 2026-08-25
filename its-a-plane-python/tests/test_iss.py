"""
Unit tests for the ISS pass-prediction module (utilities/iss.py).

Covers the pure logic that runs without the optional `ephem` dependency:
  - azimuth -> 16-point compass conversion
  - TLE disk cache handling + CelesTrak fetch (mocked requests)
  - alert-window and active-pass logic with injected pass data
  - feature gating (ISS_ENABLED off, ephem missing, no home location)
  - atomic cache writes

The whole suite must pass WITHOUT ephem installed.
"""

import json
import math
import os
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta

import pytest

# Ensure the project root is on sys.path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set env defaults BEFORE importing config: this file may be collected before
# test_overhead_utils.py, and config caches env values at first import
# (test_overhead_utils.py relies on the same defaults).
os.environ.setdefault("ZONE_TL_LAT", "51.595")
os.environ.setdefault("ZONE_TL_LON", "-0.314")
os.environ.setdefault("ZONE_BR_LAT", "51.47")
os.environ.setdefault("ZONE_BR_LON", "-0.111")
os.environ.setdefault("HOME_LAT", "51.55864")
os.environ.setdefault("HOME_LON", "-0.177332")
os.environ.setdefault("DISTANCE_UNITS", "imperial")
os.environ.setdefault("PLANE_TRACKER_DATA_DIR", tempfile.mkdtemp())

import config
from utilities import iss


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def isolate_iss_state(tmp_path, monkeypatch):
    """Reset module state and point disk caches at a temp dir for each test."""
    monkeypatch.setattr(iss, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(iss, "_CACHE_FILE", str(tmp_path / "iss.json"))
    monkeypatch.setattr(iss, "_TLE_CACHE_FILE", str(tmp_path / "iss_tle.json"))
    monkeypatch.setattr(iss, "_tle_lines", None)
    monkeypatch.setattr(iss, "_tle_ts", 0.0)
    monkeypatch.setattr(iss, "_tle_fetch_failed_until", 0.0)
    monkeypatch.setattr(iss, "_cached_passes", None)
    monkeypatch.setattr(iss, "_cached_ts", 0.0)
    monkeypatch.setattr(iss, "_next_retry_after", 0.0)
    monkeypatch.setattr(iss, "_consecutive_failures", 0)
    monkeypatch.setattr(iss, "_refresh_pending", False)
    yield


@pytest.fixture
def enabled(monkeypatch):
    """Enable the feature: config flags on and a fake truthy ephem module
    (the injected-pass code paths never call into ephem itself)."""
    monkeypatch.setattr(config, "ISS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "LOCATION_HOME", [40.7, -74.0], raising=False)
    monkeypatch.setattr(iss, "ephem", object())
    yield


def make_pass(rise_in_sec, duration=360, elevation=45.0, visible=False,
              rise_compass="NW", set_compass="SE", now=None):
    """Build a pass dict whose rise time is `rise_in_sec` from now.

    Times are serialised with whole-second precision, so round `now` UP to
    the next second: the stored rise offset is then >= `rise_in_sec`,
    keeping minute-bucket expectations (e.g. 240s -> "ISS 4m") stable.
    """
    if now is None:
        now = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=1)
    rise = now + timedelta(seconds=rise_in_sec)
    set_ = rise + timedelta(seconds=duration)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return {
        "rise": {"time": rise.strftime(fmt), "compass": rise_compass},
        "set": {"time": set_.strftime(fmt), "compass": set_compass},
        "culmination": {"elevation_deg": elevation},
        "duration_sec": duration,
        "visible": visible,
    }


def inject_passes(monkeypatch, passes):
    """Make _refresh() serve `passes` without touching disk or threads."""
    monkeypatch.setattr(iss, "_cached_passes", passes)
    monkeypatch.setattr(iss, "_cached_ts", time.time())
    monkeypatch.setattr(iss, "_next_retry_after", time.time() + 3600)


# ═══════════════════════════════════════════════════════════════════════════
# Azimuth -> compass
# ═══════════════════════════════════════════════════════════════════════════

class TestAzToCompass:
    @pytest.mark.parametrize("deg,expected", [
        (0, "N"),
        (11.24, "N"),
        (11.3, "NNE"),
        (45, "NE"),
        (90, "E"),
        (135, "SE"),
        (180, "S"),
        (225, "SW"),
        (270, "W"),
        (315, "NW"),
        (337.5, "NNW"),
        (348.75, "N"),   # wraps back to N
        (359.9, "N"),
        (360, "N"),
        (405, "NE"),     # > 360 normalised
    ])
    def test_direction(self, deg, expected):
        assert iss._az_to_compass(math.radians(deg)) == expected


# ═══════════════════════════════════════════════════════════════════════════
# TLE cache handling (mocked requests)
# ═══════════════════════════════════════════════════════════════════════════

TLE_TEXT = (
    "ISS (ZARYA)\n"
    "1 25544U 98067A   26055.50000000  .00016717  00000-0  10270-3 0  9000\n"
    "2 25544  51.6400 208.9163 0006703 130.5360 325.0288 15.49560000000000\n"
)


class FakeResponse:
    def __init__(self, text="", status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise iss.requests.HTTPError(f"{self.status_code}")


class TestTLECache:
    def test_fetch_success_writes_cache(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            iss.requests, "get",
            lambda url, timeout: calls.append(url) or FakeResponse(TLE_TEXT),
        )
        assert iss._fetch_tle() is True
        assert len(calls) == 1
        assert iss._tle_lines[0] == "ISS (ZARYA)"
        assert iss._tle_lines[1].startswith("1 25544U")
        # Cache file written atomically: valid JSON, no .tmp leftover
        with open(iss._TLE_CACHE_FILE) as f:
            obj = json.load(f)
        assert obj["name"] == "ISS (ZARYA)"
        assert obj["line2"].startswith("2 25544")
        assert obj["ts"] == pytest.approx(time.time(), abs=5)
        assert not os.path.exists(iss._TLE_CACHE_FILE + ".tmp")

    def test_fetch_failure_backs_off(self, monkeypatch):
        calls = []

        def failing_get(url, timeout):
            calls.append(url)
            raise iss.requests.ConnectionError("no network")

        monkeypatch.setattr(iss.requests, "get", failing_get)
        assert iss._fetch_tle() is False
        # Second call within the backoff window must NOT hit the network
        assert iss._fetch_tle() is False
        assert len(calls) == 1

    def test_fetch_429_backs_off(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            iss.requests, "get",
            lambda url, timeout: calls.append(url) or FakeResponse("", 429),
        )
        assert iss._fetch_tle() is False
        assert iss._fetch_tle() is False
        assert len(calls) == 1

    def test_fetch_short_payload_rejected(self, monkeypatch):
        monkeypatch.setattr(
            iss.requests, "get",
            lambda url, timeout: FakeResponse("garbage single line"),
        )
        assert iss._fetch_tle() is False
        assert iss._tle_lines is None

    def test_load_cache_fresh(self):
        iss._atomic_write_json(iss._TLE_CACHE_FILE, {
            "ts": time.time(), "name": "ISS (ZARYA)",
            "line1": "1 ...", "line2": "2 ...",
        })
        assert iss._load_tle_cache() is True
        assert iss._tle_lines == ("ISS (ZARYA)", "1 ...", "2 ...")

    def test_load_cache_too_old_rejected(self):
        iss._atomic_write_json(iss._TLE_CACHE_FILE, {
            "ts": time.time() - iss._TLE_REFRESH * 2 - 10,
            "name": "ISS (ZARYA)", "line1": "1 ...", "line2": "2 ...",
        })
        assert iss._load_tle_cache() is False
        assert iss._tle_lines is None

    def test_load_cache_corrupt_json(self):
        with open(iss._TLE_CACHE_FILE, "w") as f:
            f.write("{not json")
        assert iss._load_tle_cache() is False

    def test_ensure_tle_uses_fresh_cache_without_network(self, monkeypatch):
        iss._atomic_write_json(iss._TLE_CACHE_FILE, {
            "ts": time.time(), "name": "ISS", "line1": "1", "line2": "2",
        })

        def no_network(*a, **k):
            raise AssertionError("network should not be used")

        monkeypatch.setattr(iss.requests, "get", no_network)
        assert iss._ensure_tle() is True

    def test_ensure_tle_serves_stale_cache_when_fetch_fails(self, monkeypatch):
        # Cache older than the refresh interval (needs refetch) but within
        # the 2x acceptance window
        iss._atomic_write_json(iss._TLE_CACHE_FILE, {
            "ts": time.time() - iss._TLE_REFRESH - 60,
            "name": "ISS", "line1": "1", "line2": "2",
        })

        def failing_get(url, timeout):
            raise iss.requests.ConnectionError("down")

        monkeypatch.setattr(iss.requests, "get", failing_get)
        assert iss._ensure_tle() is True   # stale TLE still served
        assert iss._tle_lines == ("ISS", "1", "2")

    def test_ensure_tle_false_with_nothing(self, monkeypatch):
        def failing_get(url, timeout):
            raise iss.requests.ConnectionError("down")

        monkeypatch.setattr(iss.requests, "get", failing_get)
        assert iss._ensure_tle() is False


# ═══════════════════════════════════════════════════════════════════════════
# Alert-window logic (injected pass data)
# ═══════════════════════════════════════════════════════════════════════════

class TestAlertWindow:
    def test_upcoming_pass_within_window(self, enabled, monkeypatch):
        inject_passes(monkeypatch, [make_pass(rise_in_sec=185)])
        alert = iss.get_iss_alert()
        assert alert is not None
        assert alert["text"] == "ISS 3m"
        assert alert["visible"] is False

    def test_visible_flag_propagates(self, enabled, monkeypatch):
        inject_passes(monkeypatch, [make_pass(rise_in_sec=300, visible=True)])
        alert = iss.get_iss_alert()
        assert alert["visible"] is True

    def test_pass_too_far_away(self, enabled, monkeypatch):
        inject_passes(monkeypatch, [make_pass(rise_in_sec=iss._ALERT_WINDOW + 120)])
        assert iss.get_iss_alert() is None

    def test_imminent_pass_shows_at_least_one_minute(self, enabled, monkeypatch):
        inject_passes(monkeypatch, [make_pass(rise_in_sec=20)])
        alert = iss.get_iss_alert()
        assert alert["text"] == "ISS 1m"

    def test_active_pass_suppresses_alert(self, enabled, monkeypatch):
        # Rose 60s ago, 360s duration -> takeover scene owns the display
        inject_passes(monkeypatch, [make_pass(rise_in_sec=-60)])
        assert iss.get_iss_alert() is None

    def test_finished_pass_ignored_but_next_alertable(self, enabled, monkeypatch):
        inject_passes(monkeypatch, [
            make_pass(rise_in_sec=-1000, duration=360),   # long over
            make_pass(rise_in_sec=240),                   # next one in 4m
        ])
        alert = iss.get_iss_alert()
        assert alert is not None
        assert alert["text"] == "ISS 4m"

    def test_no_passes_no_alert(self, enabled, monkeypatch):
        inject_passes(monkeypatch, [])
        assert iss.get_iss_alert() is None

    def test_malformed_pass_skipped(self, enabled, monkeypatch):
        inject_passes(monkeypatch, [
            {"rise": {"time": "not-a-date"}},
            {"rise": {}},
            make_pass(rise_in_sec=120),
        ])
        alert = iss.get_iss_alert()
        assert alert is not None
        assert alert["text"] == "ISS 2m"


# ═══════════════════════════════════════════════════════════════════════════
# Active pass data (takeover scene input)
# ═══════════════════════════════════════════════════════════════════════════

class TestActivePass:
    def test_no_active_pass(self, enabled, monkeypatch):
        inject_passes(monkeypatch, [make_pass(rise_in_sec=300)])
        assert iss.get_iss_pass_data() is None

    def test_active_pass_fields(self, enabled, monkeypatch):
        inject_passes(monkeypatch, [
            make_pass(rise_in_sec=-180, duration=360, elevation=67.5,
                      visible=True, rise_compass="SW", set_compass="NE"),
        ])
        data = iss.get_iss_pass_data()
        assert data is not None
        assert data["is_active"] is True
        assert data["rise_compass"] == "SW"
        assert data["set_compass"] == "NE"
        assert data["max_elevation"] == 67.5
        assert data["visible"] is True
        assert data["duration_sec"] == 360
        assert data["progress"] == pytest.approx(0.5, abs=0.02)
        assert 170 <= data["time_remaining_sec"] <= 185

    def test_progress_clamped(self, enabled, monkeypatch):
        # Rise essentially "now"
        inject_passes(monkeypatch, [make_pass(rise_in_sec=0, duration=360)])
        data = iss.get_iss_pass_data()
        if data is not None:  # depending on second boundary it may be active
            assert 0.0 <= data["progress"] <= 1.0

    def test_find_active_pass_with_injected_now(self):
        now = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)
        passes = [make_pass(rise_in_sec=-100, duration=300, now=now)]
        p, since = iss._find_active_pass(passes, now=now)
        assert p is passes[0]
        assert since == pytest.approx(100, abs=1)

    def test_find_active_pass_none_when_over(self):
        now = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)
        passes = [make_pass(rise_in_sec=-500, duration=300, now=now)]
        p, since = iss._find_active_pass(passes, now=now)
        assert p is None
        assert since == 0


# ═══════════════════════════════════════════════════════════════════════════
# Feature gating (headless / disabled behaviour)
# ═══════════════════════════════════════════════════════════════════════════

class TestFeatureGating:
    def _forbid_threads(self, monkeypatch):
        def no_thread(*a, **k):
            raise AssertionError("no background thread should be spawned")
        monkeypatch.setattr(iss.threading, "Thread", no_thread)

    def test_disabled_without_ephem(self, monkeypatch):
        # The venv has no ephem, but force it explicitly for clarity
        monkeypatch.setattr(iss, "ephem", None)
        monkeypatch.setattr(config, "ISS_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "LOCATION_HOME", [40.7, -74.0], raising=False)
        self._forbid_threads(monkeypatch)
        assert iss.get_iss_alert() is None
        assert iss.get_iss_pass_data() is None
        assert iss.is_iss_visible_now(40.7, -74.0) is False
        assert iss.get_iss_position() is None

    def test_disabled_by_config_flag(self, monkeypatch):
        monkeypatch.setattr(iss, "ephem", object())
        monkeypatch.setattr(config, "ISS_ENABLED", False, raising=False)
        monkeypatch.setattr(config, "LOCATION_HOME", [40.7, -74.0], raising=False)
        self._forbid_threads(monkeypatch)
        assert iss.get_iss_alert() is None
        assert iss.get_iss_pass_data() is None

    def test_disabled_without_home_location(self, monkeypatch):
        monkeypatch.setattr(iss, "ephem", object())
        monkeypatch.setattr(config, "ISS_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "LOCATION_HOME", [0.0, 0.0], raising=False)
        self._forbid_threads(monkeypatch)
        assert iss.get_iss_alert() is None
        assert iss.get_iss_pass_data() is None

    def test_module_imports_without_ephem(self):
        # The test venv has no ephem installed; the import at the top of
        # this file already proves this, but make it explicit.
        assert hasattr(iss, "get_iss_alert")
        assert hasattr(iss, "get_iss_pass_data")


# ═══════════════════════════════════════════════════════════════════════════
# Disk cache (pass predictions)
# ═══════════════════════════════════════════════════════════════════════════

class TestPassDiskCache:
    def test_cold_start_loads_disk_cache(self, enabled, monkeypatch):
        passes = [make_pass(rise_in_sec=240)]
        iss._atomic_write_json(iss._CACHE_FILE, {"ts": time.time(), "passes": passes})

        # Fresh disk cache means no background thread needed
        def no_thread(*a, **k):
            raise AssertionError("no background thread should be spawned")
        monkeypatch.setattr(iss.threading, "Thread", no_thread)

        alert = iss.get_iss_alert()
        assert alert is not None
        assert alert["text"] == "ISS 4m"

    def test_stale_disk_cache_triggers_background_compute(self, enabled, monkeypatch):
        iss._atomic_write_json(iss._CACHE_FILE, {
            "ts": time.time() - iss._POLL_INTERVAL * 3,
            "passes": [make_pass(rise_in_sec=240)],
        })

        started = []

        class FakeThread:
            def __init__(self, target=None, args=(), daemon=None):
                started.append((target, args))

            def start(self):
                pass

        monkeypatch.setattr(iss.threading, "Thread", FakeThread)
        result = iss._refresh()
        assert result == []          # stale cache rejected, nothing yet
        assert len(started) == 1     # compute scheduled in background
        assert started[0][1] == (40.7, -74.0)

    def test_load_cache_respects_max_age(self):
        iss._atomic_write_json(iss._CACHE_FILE, {
            "ts": time.time() - 7200, "passes": [{"x": 1}],
        })
        passes, ts = iss._load_cache()                 # default: 2x poll = 1h
        assert passes is None
        passes, ts = iss._load_cache(max_age=86400)    # generous window
        assert passes == [{"x": 1}]

    def test_atomic_write_never_raises(self, tmp_path):
        # Target path whose parent is a *file* -> open() fails; must not raise
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        iss._atomic_write_json(str(blocker / "sub" / "cache.json"), {"a": 1})
