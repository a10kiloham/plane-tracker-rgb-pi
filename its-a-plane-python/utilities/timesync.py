"""
timesync.py — is the system clock trustworthy?

A Raspberry Pi has no battery-backed hardware clock: without internet it
cannot reach NTP, boots with the last saved timestamp, and shows a wrong
time that reboots can never fix. The display should SHOW that state rather
than silently presenting a wrong clock as fact.

is_time_synchronized() asks systemd (timedatectl NTPSynchronized):
    True   — clock is NTP-synchronized, trust it
    False  — explicitly NOT synchronized (no network / NTP blocked)
    None   — cannot determine (no timedatectl, e.g. Docker); assume OK

The answer is cached for 60s so the render path never pays the subprocess
cost more than once a minute.
"""

import logging
import subprocess
import time

logger = logging.getLogger(__name__)

_CACHE_SECS = 60.0
_cache = {"checked": None, "synced": None}


def _query_ntp_synchronized():
    """One timedatectl query. Returns True/False/None (unknown)."""
    try:
        out = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0:
            val = out.stdout.strip().lower()
            if val == "yes":
                return True
            if val == "no":
                return False
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def is_time_synchronized():
    """Cached (60s) NTP-sync state: True / False / None (unknown)."""
    now = time.monotonic()
    if _cache["checked"] is not None and (now - _cache["checked"]) < _CACHE_SECS:
        return _cache["synced"]

    synced = _query_ntp_synchronized()
    if synced is False and _cache["synced"] is not False:
        logger.warning(
            "System clock is NOT NTP-synchronized — the Pi has no hardware "
            "clock, so the displayed time is untrustworthy until network/NTP "
            "returns (clock shown in red)."
        )
    elif synced is True and _cache["synced"] is False:
        logger.info("System clock NTP synchronization restored.")
    _cache.update(checked=now, synced=synced)
    return synced
