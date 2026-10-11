"""Tests for utilities/timesync.py (NTP sync detection with caching)."""

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DISTANCE_UNITS", "imperial")
os.environ.setdefault("PLANE_TRACKER_DATA_DIR", tempfile.mkdtemp())

from utilities import timesync


class FakeResult:
    def __init__(self, rc, out):
        self.returncode = rc
        self.stdout = out


def _reset():
    timesync._cache.update(checked=None, synced=None)


def test_parses_yes_no(monkeypatch):
    _reset()
    monkeypatch.setattr(timesync.subprocess, "run",
                        lambda *a, **k: FakeResult(0, "yes\n"))
    assert timesync.is_time_synchronized() is True
    _reset()
    monkeypatch.setattr(timesync.subprocess, "run",
                        lambda *a, **k: FakeResult(0, "no\n"))
    assert timesync.is_time_synchronized() is False


def test_missing_timedatectl_is_unknown(monkeypatch):
    _reset()
    def boom(*a, **k):
        raise FileNotFoundError("timedatectl")
    monkeypatch.setattr(timesync.subprocess, "run", boom)
    assert timesync.is_time_synchronized() is None


def test_garbage_output_is_unknown(monkeypatch):
    _reset()
    monkeypatch.setattr(timesync.subprocess, "run",
                        lambda *a, **k: FakeResult(0, "maybe\n"))
    assert timesync.is_time_synchronized() is None
    _reset()
    monkeypatch.setattr(timesync.subprocess, "run",
                        lambda *a, **k: FakeResult(1, ""))
    assert timesync.is_time_synchronized() is None


def test_result_is_cached(monkeypatch):
    _reset()
    calls = {"n": 0}
    def counting(*a, **k):
        calls["n"] += 1
        return FakeResult(0, "yes\n")
    monkeypatch.setattr(timesync.subprocess, "run", counting)
    for _ in range(10):
        assert timesync.is_time_synchronized() is True
    assert calls["n"] == 1  # one subprocess call despite 10 queries
