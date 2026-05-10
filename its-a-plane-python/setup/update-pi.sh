#!/bin/bash
# update-pi.sh — Run this ON THE RASPBERRY PI to switch from the old repo
# to your fork and install everything.
#
# Usage (on Pi):
#   curl -sSL https://raw.githubusercontent.com/a10kiloham/plane-tracker-rgb-pi/main/its-a-plane-python/setup/update-pi.sh | sudo bash
#
# Or if you've already cloned manually:
#   cd ~/plane-tracker-rgb-pi && sudo bash its-a-plane-python/setup/update-pi.sh
#
set -euo pipefail

REPO_DIR="$HOME/plane-tracker-rgb-pi"
FORK_URL="https://github.com/a10kiloham/plane-tracker-rgb-pi.git"
ENV_DEST="/etc/plane-tracker.env"
RGB_MATRIX_DIR="$REPO_DIR/rpi-rgb-led-matrix"

echo "============================================"
echo "  Plane Tracker — Switch to forked repo"
echo "============================================"
echo ""

# --- Step 1: Switch git remote or fresh clone ---
if [ -d "$REPO_DIR/.git" ]; then
    echo "==> Existing repo found at $REPO_DIR"
    cd "$REPO_DIR"

    # Stash any local changes
    git stash 2>/dev/null || true

    # Update remote to your fork
    echo "==> Updating origin remote to $FORK_URL"
    git remote set-url origin "$FORK_URL"

    # Fetch and reset to latest
    echo "==> Pulling latest from your fork..."
    git fetch origin
    git checkout main 2>/dev/null || git checkout master
    git reset --hard "origin/$(git rev-parse --abbrev-ref HEAD)"
else
    echo "==> Cloning your fork to $REPO_DIR"
    git clone "$FORK_URL" "$REPO_DIR"
    cd "$REPO_DIR"
fi

echo ""

# --- Step 1b: Install Adafruit RGB Matrix helper library if not present ---
if [ ! -d "$RGB_MATRIX_DIR" ]; then
    echo "==> rpi-rgb-led-matrix not found in $REPO_DIR"
    echo "==> Downloading and running Adafruit RGB Matrix installer..."
    curl -sSL https://raw.githubusercontent.com/adafruit/Raspberry-Pi-Installer-Scripts/main/rgb-matrix.sh | bash
    echo "   ✓ RGB Matrix helper library installed"
else
    echo "==> rpi-rgb-led-matrix already present, skipping installer"
fi

echo ""

# --- Step 1c: Extract airline logos from logo.zip ---
if [ -f "$REPO_DIR/logo.zip" ]; then
    echo "==> Extracting logos from logo.zip..."
    mkdir -p "$REPO_DIR/its-a-plane-python/logos"
    unzip -qo "$REPO_DIR/logo.zip" -d "$REPO_DIR/its-a-plane-python/logos"
    chmod -R a+r "$REPO_DIR/its-a-plane-python/logos"
    echo "   ✓ Logos extracted and permissions set"
else
    echo "   ⚠ logo.zip not found in $REPO_DIR, skipping logo extraction"
fi

echo ""

# --- Step 2: Install Python dependencies (using a virtual environment) ---
echo "==> Installing system packages..."
apt update -qq
apt install -y -qq \
    build-essential python3-pip python3-venv python3-dev \
    python3-setuptools python3-wheel \
    cython3 libgraphicsmagick++1-dev \
    unzip git curl 2>/dev/null

VENV_DIR="$REPO_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "==> Creating virtual environment at $VENV_DIR"
    python3 -m venv --system-site-packages "$VENV_DIR"
else
    echo "==> Virtual environment already exists at $VENV_DIR"
fi

echo "==> Installing Python dependencies into venv..."
"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel 2>&1 | tail -3
"$VENV_DIR/bin/python" -m pip install -r "$REPO_DIR/requirements.txt" 2>&1 | tail -5

# Install rgbmatrix from source if the rpi-rgb-led-matrix repo exists
if [ -d "$RGB_MATRIX_DIR/bindings/python" ]; then
    echo "==> Installing rgbmatrix Python bindings into venv..."
    cd "$RGB_MATRIX_DIR/bindings/python"
    "$VENV_DIR/bin/python" -m pip install -e . 2>&1 | tail -3
    cd "$REPO_DIR"
else
    echo "   ⚠ rgbmatrix bindings not found at $RGB_MATRIX_DIR/bindings/python"
    echo "     Run the Adafruit RGB Matrix installer first, or:"
    echo "     cd $RGB_MATRIX_DIR/bindings/python && $VENV_DIR/bin/pip install -e ."
fi
echo "   ✓ Dependencies installed"

echo ""

# --- Step 3: Create environment file with secrets ---
if [ ! -f "$ENV_DEST" ]; then
    echo "==> Creating $ENV_DEST (you'll be prompted for your keys)"
    echo ""

    read -rp "  FR24 API Key (subscription_key|token): " FR24_KEY
    read -rp "  Tomorrow.io API Key: " TOMORROW_KEY

    cat > "$ENV_DEST" <<EOF
FR24_API_KEY=${FR24_KEY}
TOMORROW_API_KEY=${TOMORROW_KEY}
EOF
    chown root:root "$ENV_DEST"
    chmod 0600 "$ENV_DEST"
    echo "  → Saved to $ENV_DEST (mode 0600)"
else
    echo "==> $ENV_DEST already exists, keeping existing keys"
fi

echo ""

# --- Step 4: Install and enable systemd service ---
echo "==> Installing systemd service..."
sed "s|__REPO_DIR__|$REPO_DIR|g" "$REPO_DIR/its-a-plane-python/setup/plane-tracker.service" > /etc/systemd/system/plane-tracker.service
chmod 0644 /etc/systemd/system/plane-tracker.service
systemctl daemon-reload
systemctl enable plane-tracker.service

echo ""
echo "============================================"
echo "  Done! Your Pi is now using your fork."
echo "============================================"
echo ""
echo "  Start:    sudo systemctl start plane-tracker"
echo "  Status:   sudo systemctl status plane-tracker"
echo "  Logs:     sudo journalctl -u plane-tracker -f"
echo "  Edit keys: sudo nano /etc/plane-tracker.env"
echo ""
echo "  To update in future: cd $REPO_DIR && git pull"
echo ""
