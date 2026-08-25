"""ATC audio port integration tests (this fork's adaptations).

Covers what we changed while porting ajplotkin/atc-audio:
  - env-var config mapping in config.py (ATC_* keys, quiet-hours composition)
  - headless behavior: manager instantiates with no optional deps (mpv,
    pychromecast, pyatv) installed and no data files present
  - overhead.write_current_overhead snapshot shape (the auto-tune input)
  - /atc/relay network guard: loopback + private LAN allowed, public refused,
    mount-code validation, browser UA on the upstream fetch
  - /api/atc/* routes respond headless with the feature disabled
"""

import importlib
import json
import os
import sys
import tempfile

import pytest

# Ensure the project root is on sys.path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ZONE_TL_LAT", "41.0")
os.environ.setdefault("ZONE_TL_LON", "-74.0")
os.environ.setdefault("ZONE_BR_LAT", "40.0")
os.environ.setdefault("ZONE_BR_LON", "-73.0")
os.environ.setdefault("HOME_LAT", "40.7")
os.environ.setdefault("HOME_LON", "-73.8")
# This module now imports utilities.overhead first (alphabetical collection);
# match the units the other test modules expect at overhead import time.
os.environ.setdefault("DISTANCE_UNITS", "imperial")
os.environ.setdefault("PLANE_TRACKER_DATA_DIR", tempfile.mkdtemp())

import utilities.atc_audio as atc_audio  # noqa: E402
from utilities import overhead  # noqa: E402


# ── config.py env mapping ────────────────────────────────────────────────────

_ATC_ENV_KEYS = ("ATC_ENABLED", "ATC_MODE", "ATC_OUTPUT", "ATC_VOLUME",
                 "ATC_QUIET_START", "ATC_QUIET_END", "ATC_STATION",
                 "ATC_AUTO_RESUME", "ATC_USB_DEVICE", "ATC_CUSTOM_FEEDS",
                 "ATC_RELAY_PORT")


def _reload_config_with(env):
    """Reload the config module with the given ATC_* env, restoring both the
    environment AND the module state afterwards (deterministically — fixture
    teardown ordering is not relied on)."""
    import config
    saved = {k: os.environ.get(k) for k in _ATC_ENV_KEYS}
    try:
        for k in _ATC_ENV_KEYS:
            os.environ.pop(k, None)
        os.environ.update(env)
        cfg = importlib.reload(config)
        return {k: getattr(cfg, k, None)
                for k in list(_ATC_ENV_KEYS) + ["ATC_QUIET_HOURS"]}
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(config)


class TestConfigEnvMapping:
    def test_defaults_off(self):
        cfg = _reload_config_with({})
        assert cfg["ATC_ENABLED"] is False
        assert cfg["ATC_MODE"] == "auto"
        assert cfg["ATC_OUTPUT"] == "usb"
        assert cfg["ATC_VOLUME"] == 70
        assert cfg["ATC_QUIET_HOURS"] == ""   # blank -> NIGHT_START/END fallback
        assert cfg["ATC_STATION"] == ""
        assert cfg["ATC_AUTO_RESUME"] is True
        assert cfg["ATC_RELAY_PORT"] == 8080

    def test_env_overrides(self):
        cfg = _reload_config_with({
            "ATC_ENABLED": "true", "ATC_MODE": "manual",
            "ATC_STATION": "kjfk_twr", "ATC_OUTPUT": "chromecast:abc",
            "ATC_VOLUME": "35", "ATC_QUIET_START": "23:00",
            "ATC_QUIET_END": "7:30", "ATC_RELAY_PORT": "6969",
        })
        assert cfg["ATC_ENABLED"] is True
        assert cfg["ATC_MODE"] == "manual"
        assert cfg["ATC_STATION"] == "kjfk_twr"
        assert cfg["ATC_OUTPUT"] == "chromecast:abc"
        assert cfg["ATC_VOLUME"] == 35
        assert cfg["ATC_QUIET_HOURS"] == "23:00-7:30"
        assert cfg["ATC_RELAY_PORT"] == 6969

    def test_quiet_hours_requires_both_ends(self):
        cfg = _reload_config_with({"ATC_QUIET_START": "23:00"})
        assert cfg["ATC_QUIET_HOURS"] == ""


# ── headless manager instantiation ───────────────────────────────────────────

@pytest.fixture
def tmp_atc_paths(tmp_path, monkeypatch):
    """Point the atc_audio module's state files at a per-test temp dir."""
    monkeypatch.setattr(atc_audio, "_STATE_FILE", str(tmp_path / "atc_audio.json"))
    monkeypatch.setattr(atc_audio, "_DISCOVERED_CACHE", str(tmp_path / "atc_discovered.json"))
    monkeypatch.setattr(atc_audio, "_OUTPUT_CACHE", str(tmp_path / "atc_outputs.json"))
    monkeypatch.setattr(atc_audio, "_AIRPLAY_CREDS", str(tmp_path / "atc_airplay_creds.json"))
    monkeypatch.setattr(atc_audio, "_OVERHEAD_FILE", str(tmp_path / "current_overhead.json"))
    return tmp_path


class TestHeadlessManager:
    def test_instantiates_with_no_data_files_and_no_optional_deps(self, tmp_atc_paths):
        # pychromecast / pyatv / zeroconf are NOT installed in the test venv —
        # this exercises every lazy-import guard on the constructor path.
        m = atc_audio.ATCAudioManager()
        st = m.status()
        assert st["enabled"] is False          # ATC_ENABLED defaults False
        assert st["playing"] is False
        assert st["mode"] in ("off", "auto", "manual")
        assert st["output"]                    # seeded from ATC_OUTPUT default
        # tick() with the feature disabled is a no-op and must not raise.
        m.tick()
        assert m.status()["playing"] is False

    def test_first_run_seeds_from_config_env(self, tmp_atc_paths, monkeypatch):
        monkeypatch.setenv("ATC_MODE", "manual")
        monkeypatch.setenv("ATC_STATION", "kjfk_twr")
        monkeypatch.setenv("ATC_OUTPUT", "usb")
        monkeypatch.setenv("ATC_VOLUME", "42")
        import config
        importlib.reload(config)
        try:
            m = atc_audio.ATCAudioManager()
            st = m.status()
            assert st["mode"] == "manual"
            assert st["station"] == "kjfk_twr"
            assert st["output"] == "usb"
            assert st["volume"] == 42
        finally:
            for key in ("ATC_MODE", "ATC_STATION", "ATC_OUTPUT", "ATC_VOLUME"):
                monkeypatch.delenv(key, raising=False)
            importlib.reload(config)

    def test_nearby_stations_is_passive(self, tmp_atc_paths):
        m = atc_audio.ATCAudioManager()
        m._home = (40.64, -73.78)   # deterministic: next to KJFK
        from unittest.mock import patch
        with patch.object(m, "_probe_feed",
                          side_effect=AssertionError("nearby_stations probed!")):
            nearby = m.nearby_stations()
        assert isinstance(nearby, list)
        assert any(ap["icao"] == "KJFK" for ap in nearby)


# ── overhead snapshot ────────────────────────────────────────────────────────

class TestCurrentOverheadSnapshot:
    def test_writes_compact_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr(overhead, "DATA_DIR", str(tmp_path))
        overhead.write_current_overhead([
            {"callsign": "UAL123", "plane_latitude": 40.7, "plane_longitude": -73.9,
             "altitude": 12000, "distance": 4.2, "origin": "EWR",
             "destination": "LAX", "airline": "United", "trail": [[1, 2]]},
        ])
        out = json.loads((tmp_path / "current_overhead.json").read_text())
        assert out == [{
            "callsign": "UAL123", "lat": 40.7, "lon": -73.9,
            "altitude": 12000, "distance": 4.2,
            "origin": "EWR", "destination": "LAX",
        }]

    def test_manager_reads_snapshot_shape(self, tmp_path, monkeypatch):
        monkeypatch.setattr(overhead, "DATA_DIR", str(tmp_path))
        overhead.write_current_overhead([
            {"callsign": "JBU42", "altitude": 5000,
             "origin": "BOS", "destination": "JFK"},
        ])
        monkeypatch.setattr(atc_audio, "_OVERHEAD_FILE",
                            str(tmp_path / "current_overhead.json"))
        m = atc_audio.ATCAudioManager.__new__(atc_audio.ATCAudioManager)
        flights = m._read_overhead()
        assert flights[0]["destination"] == "JFK"
        assert flights[0]["altitude"] == 5000

    def test_never_raises(self, monkeypatch):
        monkeypatch.setattr(overhead, "DATA_DIR", "/nonexistent/nope")
        overhead.write_current_overhead([{"callsign": "X"}])   # must not raise


# ── web routes: relay guard + headless /api/atc/* ────────────────────────────

flask = pytest.importorskip("flask")
from web.app import app  # noqa: E402


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class _FakeUpstream:
    status_code = 200
    headers = {"Content-Type": "audio/mpeg"}

    def __init__(self):
        self.closed = False

    def iter_content(self, n):
        yield b"ID3fakeaudio"

    def close(self):
        self.closed = True


class TestRelayGuard:
    def test_public_ip_refused(self, client):
        r = client.get("/atc/relay?code=kjfk_twr",
                       environ_base={"REMOTE_ADDR": "8.8.8.8"})
        assert r.status_code == 403

    def test_loopback_allowed_past_guard(self, client):
        # Bad code proves we got past the network guard without touching
        # the upstream fetch.
        r = client.get("/atc/relay?code=kjfk;rm",
                       environ_base={"REMOTE_ADDR": "127.0.0.1"})
        assert r.status_code == 400

    def test_private_lan_allowed_past_guard(self, client):
        # Chromecast on the LAN pulls the relay — RFC1918 must be allowed.
        r = client.get("/atc/relay",
                       environ_base={"REMOTE_ADDR": "192.168.1.50"})
        assert r.status_code == 400   # past the guard; missing code

    def test_bad_codes_rejected(self, client):
        for code in ("", "kjfk twr", "kjfk.twr", "kjfk/../x", "käfk"):
            r = client.get(f"/atc/relay?code={code}",
                           environ_base={"REMOTE_ADDR": "127.0.0.1"})
            assert r.status_code == 400, code

    def test_upstream_fetch_uses_browser_ua(self, client, monkeypatch):
        import requests as _rq
        import shutil as _sh
        calls = {}

        def fake_get(url, **kw):
            calls["url"] = url
            calls["ua"] = kw.get("headers", {}).get("User-Agent", "")
            return _FakeUpstream()

        monkeypatch.setattr(_sh, "which", lambda name: None)  # no ffmpeg path
        monkeypatch.setattr(_rq, "get", fake_get)
        r = client.get("/atc/relay?code=kjfk_twr",
                       environ_base={"REMOTE_ADDR": "127.0.0.1"})
        assert r.status_code == 200
        assert r.data == b"ID3fakeaudio"
        assert calls["url"] == "https://d.liveatc.net/kjfk_twr"
        assert "Mozilla/5.0" in calls["ua"]          # browser UA, not python-requests
        # The semaphore slot must be released once the response is consumed.
        import web.app as app_module
        assert app_module._RELAY_SEMAPHORE._value == 4


class TestAtcRoutesHeadless:
    def test_status_disabled(self, client, tmp_atc_paths):
        r = client.get("/api/atc/status")
        assert r.status_code == 200
        data = r.get_json()
        assert data["enabled"] is False
        assert data["playing"] is False

    def test_nearby_stations_route(self, client, tmp_atc_paths):
        r = client.get("/api/atc/stations?nearby=1")
        assert r.status_code == 200
        assert isinstance(r.get_json()["nearby"], list)

    def test_outputs_route_headless(self, client, tmp_atc_paths):
        r = client.get("/api/atc/outputs")
        assert r.status_code == 200
        ids = {o["id"] for o in r.get_json()["outputs"]}
        assert "usb" in ids          # always present, even with no cast/airplay

    def test_atc_page_renders(self, client):
        r = client.get("/atc")
        assert r.status_code == 200
        assert b"ATC Radio" in r.data
