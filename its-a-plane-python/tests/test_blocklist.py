"""
Tests for the plane blocklist (blocked_planes.json).

Covers:
  - load_blocklist(): missing file, corrupt file, non-dict JSON, normalization,
    mtime-based reload
  - is_blocked(): exact callsign match, prefix-star match, registration match,
    case-insensitivity, empty-entry safety
  - Web routes (/blocklist/*) — skipped if flask is not installed
"""

import sys
import os
import json
import tempfile

import pytest

# Ensure the project root is on sys.path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ZONE_TL_LAT", "51.595")
os.environ.setdefault("ZONE_TL_LON", "-0.314")
os.environ.setdefault("ZONE_BR_LAT", "51.47")
os.environ.setdefault("ZONE_BR_LON", "-0.111")
os.environ.setdefault("HOME_LAT", "51.55864")
os.environ.setdefault("HOME_LON", "-0.177332")
os.environ.setdefault("DISTANCE_UNITS", "imperial")
os.environ.setdefault("PLANE_TRACKER_DATA_DIR", tempfile.mkdtemp())

from utilities import overhead
from utilities.overhead import is_blocked, load_blocklist


@pytest.fixture
def blocklist_file(tmp_path, monkeypatch):
    """Point overhead at a per-test blocklist file with a clean cache."""
    path = tmp_path / "blocked_planes.json"
    monkeypatch.setattr(overhead, "BLOCKLIST_FILE", str(path))
    monkeypatch.setattr(overhead, "_blocklist_cache",
                        {"mtime": None, "callsigns": [], "registrations": []})
    return path


def write_blocklist(path, data):
    path.write_text(json.dumps(data) if not isinstance(data, str) else data,
                    encoding="utf-8")


# ── load_blocklist ────────────────────────────────────────────────────────────

class TestLoadBlocklist:
    def test_missing_file_returns_empty(self, blocklist_file):
        result = load_blocklist()
        assert result == {"callsigns": [], "registrations": []}

    def test_corrupt_file_returns_empty(self, blocklist_file):
        write_blocklist(blocklist_file, "{not valid json!!!")
        result = load_blocklist()
        assert result == {"callsigns": [], "registrations": []}

    def test_non_dict_json_returns_empty(self, blocklist_file):
        write_blocklist(blocklist_file, ["UAL123"])
        result = load_blocklist()
        assert result == {"callsigns": [], "registrations": []}

    def test_normalizes_case_and_whitespace(self, blocklist_file):
        write_blocklist(blocklist_file, {
            "callsigns": [" ual123 ", "ryr*"],
            "registrations": ["n123ab"],
        })
        result = load_blocklist()
        assert result["callsigns"] == ["UAL123", "RYR*"]
        assert result["registrations"] == ["N123AB"]

    def test_missing_keys_tolerated(self, blocklist_file):
        write_blocklist(blocklist_file, {"callsigns": ["UAL1"]})
        result = load_blocklist()
        assert result == {"callsigns": ["UAL1"], "registrations": []}

    def test_blank_entries_dropped(self, blocklist_file):
        write_blocklist(blocklist_file, {"callsigns": ["", "  ", "DAL5"]})
        assert load_blocklist()["callsigns"] == ["DAL5"]

    def test_reload_on_mtime_change(self, blocklist_file):
        write_blocklist(blocklist_file, {"callsigns": ["UAL1"]})
        assert load_blocklist()["callsigns"] == ["UAL1"]

        write_blocklist(blocklist_file, {"callsigns": ["DAL2"]})
        # Force a different mtime even on coarse-grained filesystems
        st = os.stat(blocklist_file)
        os.utime(blocklist_file, (st.st_atime, st.st_mtime + 10))
        assert load_blocklist()["callsigns"] == ["DAL2"]

    def test_cached_between_calls_without_mtime_change(self, blocklist_file):
        write_blocklist(blocklist_file, {"callsigns": ["UAL1"]})
        first = load_blocklist()
        # Mutating the returned dict must not corrupt the cache
        first["callsigns"].append("HAX")
        assert load_blocklist()["callsigns"] == ["UAL1"]

    def test_file_deleted_after_load(self, blocklist_file):
        write_blocklist(blocklist_file, {"callsigns": ["UAL1"]})
        assert load_blocklist()["callsigns"] == ["UAL1"]
        os.unlink(blocklist_file)
        assert load_blocklist() == {"callsigns": [], "registrations": []}


# ── is_blocked ────────────────────────────────────────────────────────────────

def bl(callsigns=(), registrations=()):
    return {"callsigns": list(callsigns), "registrations": list(registrations)}


class TestIsBlocked:
    def test_exact_callsign_match(self):
        assert is_blocked("UAL1234", "", bl(callsigns=["UAL1234"]))

    def test_exact_callsign_case_insensitive(self):
        assert is_blocked("ual1234", "N999XX", bl(callsigns=["UAL1234"]))

    def test_plain_entry_is_not_a_prefix(self):
        # "RYR" must NOT block RYR123 — only the literal callsign "RYR"
        assert not is_blocked("RYR123", "", bl(callsigns=["RYR"]))
        assert is_blocked("RYR", "", bl(callsigns=["RYR"]))

    def test_star_suffix_is_prefix_match(self):
        entries = bl(callsigns=["RYR*"])
        assert is_blocked("RYR123", "", entries)
        assert is_blocked("ryr9xy", "", entries)
        assert not is_blocked("DLH123", "", entries)

    def test_lone_star_blocks_nothing(self):
        assert not is_blocked("UAL1", "", bl(callsigns=["*"]))

    def test_registration_exact_match(self):
        assert is_blocked("SWA100", "N123AB", bl(registrations=["N123AB"]))
        assert not is_blocked("SWA100", "N123AC", bl(registrations=["N123AB"]))

    def test_registration_matches_callsign_for_ga(self):
        # GA aircraft often fly with their registration as the callsign
        assert is_blocked("N123AB", "", bl(registrations=["N123AB"]))

    def test_registration_case_insensitive(self):
        assert is_blocked("SWA100", "n123ab", bl(registrations=["N123AB"]))

    def test_empty_blocklist_blocks_nothing(self):
        assert not is_blocked("UAL1", "N1", bl())

    def test_empty_callsign_not_blocked_by_exact(self):
        assert not is_blocked("", "", bl(callsigns=["UAL1"]))

    def test_none_inputs(self):
        assert not is_blocked(None, None, bl(callsigns=["UAL1"], registrations=["N1AB"]))


# ── Web routes (optional — requires flask) ────────────────────────────────────

flask = pytest.importorskip("flask")


@pytest.fixture
def client(blocklist_file):
    from web.app import app as flask_app
    import web.app as webapp
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


class TestBlocklistRoutes:
    def test_json_empty(self, client):
        resp = client.get("/blocklist/json")
        assert resp.status_code == 200
        assert resp.get_json() == {"callsigns": [], "registrations": []}

    def test_add_and_remove_callsign(self, client, blocklist_file):
        resp = client.post("/blocklist/add",
                           json={"type": "callsigns", "value": "ual1234"})
        assert resp.status_code == 200
        assert resp.get_json()["callsigns"] == ["UAL1234"]
        # Written to disk in the expected shape
        on_disk = json.loads(blocklist_file.read_text())
        assert on_disk["callsigns"] == ["UAL1234"]

        resp = client.post("/blocklist/remove",
                           json={"type": "callsigns", "value": "UAL1234"})
        assert resp.status_code == 200
        assert resp.get_json()["callsigns"] == []

    def test_add_registration(self, client):
        resp = client.post("/blocklist/add",
                           json={"type": "registrations", "value": "N123AB"})
        assert resp.status_code == 200
        assert resp.get_json()["registrations"] == ["N123AB"]

    def test_add_prefix_callsign_allowed(self, client):
        resp = client.post("/blocklist/add",
                           json={"type": "callsigns", "value": "RYR*"})
        assert resp.status_code == 200
        assert "RYR*" in resp.get_json()["callsigns"]

    def test_add_prefix_registration_rejected(self, client):
        resp = client.post("/blocklist/add",
                           json={"type": "registrations", "value": "N123*"})
        assert resp.status_code == 400

    def test_add_invalid_type_rejected(self, client):
        resp = client.post("/blocklist/add",
                           json={"type": "bogus", "value": "UAL1"})
        assert resp.status_code == 400

    def test_add_invalid_value_rejected(self, client):
        for bad in ["", "A", "TOOLONGCALLSIGN1", "UA L1", "UAL1!"]:
            resp = client.post("/blocklist/add",
                               json={"type": "callsigns", "value": bad})
            assert resp.status_code == 400, bad

    def test_remove_missing_entry_404(self, client):
        resp = client.post("/blocklist/remove",
                           json={"type": "callsigns", "value": "NOPE1"})
        assert resp.status_code == 404

    def test_add_duplicate_is_noop(self, client):
        client.post("/blocklist/add", json={"type": "callsigns", "value": "UAL1"})
        resp = client.post("/blocklist/add", json={"type": "callsigns", "value": "UAL1"})
        assert resp.status_code == 200
        assert resp.get_json()["callsigns"] == ["UAL1"]
