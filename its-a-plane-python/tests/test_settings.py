"""Tests for the settings system (UI > env > default), the settings API,
and the blocked-airports feature."""

import json
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DISTANCE_UNITS", "imperial")
os.environ.setdefault("PLANE_TRACKER_DATA_DIR", tempfile.mkdtemp())

from utilities import settings_registry as reg
from utilities.overhead import is_blocked_airport

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ─── Registry validation ─────────────────────────────────────────────────────

def test_int_validation():
    assert reg.validate("BRIGHTNESS", "50") == ("50", None)
    assert reg.validate("BRIGHTNESS", " 07 ") == ("7", None)
    assert reg.validate("BRIGHTNESS", "150")[1] is not None      # max 100
    assert reg.validate("BRIGHTNESS", "abc")[1] is not None
    assert reg.validate("BRIGHTNESS", "") == ("", None)          # clear = valid


def test_bool_time_choice_validation():
    assert reg.validate("ISS_ENABLED", "yes") == ("True", None)
    assert reg.validate("ISS_ENABLED", "0") == ("False", None)
    assert reg.validate("ISS_ENABLED", "maybe")[1] is not None
    assert reg.validate("NIGHT_START", "22:30") == ("22:30", None)
    assert reg.validate("NIGHT_START", "25:00")[1] is not None
    assert reg.validate("CLOCK_FORMAT", "12hr") == ("12hr", None)
    assert reg.validate("CLOCK_FORMAT", "13hr")[1] is not None
    assert reg.validate("NOT_A_SETTING", "x")[1] == "unknown setting"


def test_registry_covers_config_and_masks():
    keys = {s["key"] for s in reg.SETTINGS}
    for expected in ("FR24_API_KEY", "BRIGHTNESS", "HOME_LAT", "ATC_ENABLED",
                     "BONNET_TYPE", "EMAIL_PASSWORD", "SERVICE_NAME"):
        assert expected in keys
    assert reg.mask_secret("") == ""
    assert reg.mask_secret("short") == "•••••"
    m = reg.mask_secret("abcdefghijklmnop")
    assert m.startswith("abcd") and m.endswith("mnop") and "•" in m


# ─── config.py precedence: settings.json > env > default ────────────────────

def _run_config(settings, env):
    """Import config in a subprocess with a temp settings.json + env."""
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "settings.json"), "w") as f:
        json.dump(settings, f)
    code = (
        "import config, json;"
        "print(json.dumps({'BRIGHTNESS': config.BRIGHTNESS,"
        " 'NIGHT_START': config.NIGHT_START,"
        " 'HOME_LAT': config.LOCATION_HOME[0],"
        " 'ISS': config.ISS_ENABLED}))"
    )
    full_env = {"PATH": os.environ.get("PATH", ""),
                "PLANE_TRACKER_DATA_DIR": tmp, **env}
    out = subprocess.run([sys.executable, "-c", code], cwd=BASE, env=full_env,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_ui_beats_env_beats_default():
    got = _run_config(
        settings={"BRIGHTNESS": "42", "ISS_ENABLED": "True"},
        env={"BRIGHTNESS": "77", "NIGHT_START": "23:15", "HOME_LAT": "40.5"},
    )
    assert got["BRIGHTNESS"] == 42          # UI wins over env
    assert got["NIGHT_START"] == "23:15"    # env wins over default
    assert got["HOME_LAT"] == 40.5          # env float parsing
    assert got["ISS"] is True               # UI bool


def test_blank_ui_value_falls_back_to_env():
    got = _run_config(settings={"BRIGHTNESS": ""}, env={"BRIGHTNESS": "66"})
    assert got["BRIGHTNESS"] == 66


def test_no_settings_file_is_pure_env_behavior():
    tmp = tempfile.mkdtemp()  # no settings.json at all
    code = "import config; print(config.BRIGHTNESS, config.NIGHT_END)"
    out = subprocess.run([sys.executable, "-c", code], cwd=BASE,
                         env={"PATH": os.environ.get("PATH", ""),
                              "PLANE_TRACKER_DATA_DIR": tmp,
                              "BRIGHTNESS": "88"},
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split()[-2:] == ["88", "06:00"]


def test_corrupt_settings_file_ignored():
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "settings.json"), "w") as f:
        f.write("{not json")
    code = "import config; print(config.BRIGHTNESS)"
    out = subprocess.run([sys.executable, "-c", code], cwd=BASE,
                         env={"PATH": os.environ.get("PATH", ""),
                              "PLANE_TRACKER_DATA_DIR": tmp},
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split()[-1] == "100"


# ─── Blocked airports ────────────────────────────────────────────────────────

def _bl(airports):
    return {"callsigns": [], "registrations": [], "airports": airports}


def test_airport_block_matches_either_endpoint():
    bl = _bl(["LAX", "EGLL"])
    assert is_blocked_airport("LAX", "JFK", bl) is True
    assert is_blocked_airport("JFK", "LAX", bl) is True
    assert is_blocked_airport("jfk", "lax", bl) is True   # case-insensitive
    assert is_blocked_airport("JFK", "ORD", bl) is False


def test_airport_block_ignores_empty_endpoints():
    bl = _bl(["LAX"])
    assert is_blocked_airport("", "", bl) is False
    assert is_blocked_airport("", "JFK", bl) is False
    assert is_blocked_airport(None, "LAX", bl) is True
    assert is_blocked_airport("X", "Y", _bl([])) is False


def test_legacy_blocklist_file_without_airports(tmp_path, monkeypatch):
    from utilities import overhead as oh
    path = tmp_path / "blocked_planes.json"
    path.write_text(json.dumps({"callsigns": ["RYR*"], "registrations": []}))
    monkeypatch.setattr(oh, "BLOCKLIST_FILE", str(path))
    monkeypatch.setattr(oh, "_blocklist_cache",
                        {"mtime": None, "callsigns": [], "registrations": [],
                         "airports": []})
    bl = oh.load_blocklist()
    assert bl["airports"] == [] and bl["callsigns"] == ["RYR*"]


# ─── Settings API + pages ────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    from web.app import app
    import config as cfg
    monkeypatch.setattr(cfg, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    app.config["TESTING"] = True
    return app.test_client()


def test_settings_get_shape(client):
    data = client.get("/api/settings").get_json()
    assert "groups" in data and len(data["groups"]) >= 5
    all_items = [i for g in data["groups"] for i in g["items"]]
    brightness = next(i for i in all_items if i["key"] == "BRIGHTNESS")
    assert brightness["type"] == "int" and brightness["default"] == "100"
    fr24 = next(i for i in all_items if i["key"] == "FR24_API_KEY")
    assert fr24["is_secret"] is True
    # Secrets must never be sent in the clear
    if fr24["env_value"]:
        assert "•" in fr24["env_value"]


def test_settings_post_save_and_clear(client, tmp_path):
    r = client.post("/api/settings", json={"BRIGHTNESS": "55", "ISS_ENABLED": "on"})
    assert r.status_code == 200 and r.get_json()["restart_needed"] is True
    stored = json.loads((tmp_path / "settings.json").read_text())
    assert stored == {"BRIGHTNESS": "55", "ISS_ENABLED": "True"}

    r = client.post("/api/settings", json={"BRIGHTNESS": ""})   # clear
    assert r.status_code == 200
    stored = json.loads((tmp_path / "settings.json").read_text())
    assert "BRIGHTNESS" not in stored and stored["ISS_ENABLED"] == "True"


def test_settings_post_validation(client):
    r = client.post("/api/settings", json={"BRIGHTNESS": "999"})
    assert r.status_code == 400
    assert "BRIGHTNESS" in r.get_json()["fields"]
    r = client.post("/api/settings", json={"TOTALLY_UNKNOWN": "1"})
    assert r.status_code == 400


def test_settings_post_ignores_masked_secret(client, tmp_path):
    r = client.post("/api/settings", json={"FR24_API_KEY": "abcd••••••wxyz"})
    assert r.status_code == 200
    stored = json.loads((tmp_path / "settings.json").read_text()) \
        if (tmp_path / "settings.json").exists() else {}
    assert "FR24_API_KEY" not in stored


def test_settings_page_and_config_redirect(client):
    assert client.get("/settings").status_code == 200
    r = client.get("/config")
    assert r.status_code == 302 and "/settings" in r.headers["Location"]


def test_blocklist_airports_roundtrip(client, tmp_path, monkeypatch):
    from utilities import overhead as oh
    monkeypatch.setattr(oh, "BLOCKLIST_FILE", str(tmp_path / "blocked_planes.json"))
    monkeypatch.setattr(oh, "_blocklist_cache",
                        {"mtime": None, "callsigns": [], "registrations": [],
                         "airports": []})
    r = client.post("/blocklist/add", json={"type": "airports", "value": "lax"})
    assert r.status_code == 200 and r.get_json()["airports"] == ["LAX"]
    r = client.post("/blocklist/add", json={"type": "airports", "value": "TOOLONG9"})
    assert r.status_code == 400
    r = client.post("/blocklist/remove", json={"type": "airports", "value": "LAX"})
    assert r.status_code == 200 and r.get_json()["airports"] == []
