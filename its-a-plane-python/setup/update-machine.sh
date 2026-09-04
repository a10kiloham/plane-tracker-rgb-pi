#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# update-machine.sh — bring any plane-tracker machine to the latest version.
#
# Run as root:
#   sudo bash /home/robk/plane-tracker-rgb-pi/its-a-plane-python/setup/update-machine.sh
#
# Handles every machine layout seen in this fleet:
#   - repo owned by root OR by a user (git "dubious ownership" handled)
#   - venv at <repo>/.venv OR system python (/usr/bin/python3)
#   - installs the systemd unit with the right python + MALLOC_ARENA_MAX=2
#   - installs the ephem dependency (venv pip / apt / pip --break-system-packages)
#   - cleans corrupted farthest.txt entries (>50,000 "km" = old meters bug)
#   - enables persistent journald (200MB cap) so crashes leave evidence
#   - restarts the service and verifies it came back healthy
#
# Idempotent: safe to re-run any time.
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

REPO="${1:-/home/robk/plane-tracker-rgb-pi}"
DATA_DIR="${PLANE_TRACKER_DATA_DIR:-/var/lib/plane-tracker}"

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo (needed for systemd, journald, $DATA_DIR)." >&2
    exit 1
fi
if [ ! -d "$REPO/.git" ]; then
    echo "ERROR: no git repo at $REPO (pass the repo path as the first argument)." >&2
    exit 1
fi

echo "── 1/7 Updating repo at $REPO ──────────────────────────────────────"
# Root pulling a repo owned by anyone triggers git's dubious-ownership guard.
git config --global --add safe.directory "$REPO" 2>/dev/null || true
# chmod -R on a checkout makes every file look modified; ignore mode-only diffs.
git -C "$REPO" config core.fileMode false

# Recover from a previously interrupted update: unmerged index entries (e.g.
# airports.json's tracked->untracked transition conflicting with a stash pop)
# block every later pull. Drop them from the index, keep the worktree files.
UNMERGED=$(git -C "$REPO" diff --name-only --diff-filter=U)
if [ -n "$UNMERGED" ]; then
    echo "Recovering unmerged index entries:"
    echo "$UNMERGED" | while read -r f; do
        git -C "$REPO" rm -q -f --cached "$f" 2>/dev/null || true
    done
fi
git -C "$REPO" reset -q || true   # unstage leftovers so stash sees a clean index

git -C "$REPO" stash -q || true
git -C "$REPO" pull --ff-only
if ! git -C "$REPO" stash pop -q 2>/dev/null; then
    # Pop conflicts happen when a stashed file stopped being tracked upstream.
    # Resolve by dropping the conflicting index entries (worktree kept) and
    # discarding the stash — local caches rebuild themselves.
    POP_UNMERGED=$(git -C "$REPO" diff --name-only --diff-filter=U)
    if [ -n "$POP_UNMERGED" ]; then
        echo "Stash pop conflicted on: $POP_UNMERGED — resolving"
        echo "$POP_UNMERGED" | while read -r f; do
            git -C "$REPO" rm -q -f --cached "$f" 2>/dev/null || true
        done
        git -C "$REPO" stash drop -q 2>/dev/null || true
    fi
fi
git -C "$REPO" log --oneline -1

echo "── 2/7 Detecting python ────────────────────────────────────────────"
if [ -x "$REPO/.venv/bin/python3" ]; then
    PY="$REPO/.venv/bin/python3"
    PIP_INSTALL="$REPO/.venv/bin/pip install -q"
else
    PY="/usr/bin/python3"
    PIP_INSTALL=""   # resolved below
fi
echo "python: $PY"

echo "── 3/7 Installing ephem (for ISS feature) ──────────────────────────"
if ! "$PY" -c "import ephem" 2>/dev/null; then
    if [ -n "$PIP_INSTALL" ]; then
        $PIP_INSTALL ephem
    else
        apt-get install -y -q python3-ephem 2>/dev/null \
            || /usr/bin/pip3 install --break-system-packages -q ephem
    fi
fi
"$PY" -c "import ephem; print('ephem', ephem.__version__)"

echo "── 4/7 Installing systemd unit (with MALLOC_ARENA_MAX=2) ───────────"
sed -e "s|__REPO_DIR__/.venv/bin/python3|$PY|" \
    -e "s|__REPO_DIR__|$REPO|g" \
    "$REPO/its-a-plane-python/setup/plane-tracker.service" \
    > /etc/systemd/system/plane-tracker.service
grep -E "^(ExecStart|Environment)=" /etc/systemd/system/plane-tracker.service

echo "── 5/7 Cleaning corrupted farthest.txt entries ─────────────────────"
"$PY" - <<PYEOF
import json
p = "$DATA_DIR/farthest.txt"
try:
    d = json.load(open(p))
except Exception:
    d = None
if isinstance(d, list):
    keep = [f for f in d if f.get("farthest_value", 0) <= 50000]
    if len(keep) != len(d):
        json.dump(keep, open(p, "w"), indent=4)
    print(f"farthest.txt: kept {len(keep)} of {len(d)} entries")
else:
    print("farthest.txt: none/empty — nothing to clean")
PYEOF

echo "── 6/7 Enabling persistent journald (200MB cap) ────────────────────"
mkdir -p /etc/systemd/journald.conf.d /var/log/journal
printf '[Journal]\nStorage=persistent\nSystemMaxUse=200M\n' \
    > /etc/systemd/journald.conf.d/persistent.conf
systemctl restart systemd-journald
journalctl --flush 2>/dev/null || true

echo "── 7/7 Restarting and verifying ────────────────────────────────────"
systemctl daemon-reload
systemctl restart plane-tracker
sleep 12
systemctl is-active plane-tracker
P=$(systemctl show plane-tracker -p MainPID --value)
tr '\0' '\n' < "/proc/$P/environ" | grep MALLOC_ARENA_MAX \
    || { echo "ERROR: MALLOC_ARENA_MAX missing from process env"; exit 1; }
awk '/VmRSS|VmData|VmSwap/' "/proc/$P/status"
code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/ || true)
echo "web UI: HTTP $code"
[ "$code" = "200" ] || echo "WARNING: web UI not responding yet (may need a few more seconds)"

echo ""
echo "✓ Update complete. Errors, if any, appear in: journalctl -u plane-tracker -f"
echo "  Optional features (all default OFF) are enabled in /etc/plane-tracker.env:"
echo "  ISS_ENABLED, LANDMARKS_ENABLED, HOURLY_CHIME_ENABLED (+ mpv & timer,"
echo "  see setup/systemd/README.md), ATC_ENABLED (see /atc page)."
