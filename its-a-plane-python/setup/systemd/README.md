# Hourly chime — systemd timer install

The chime is fired by a systemd timer, **not** from inside the tracker
process. An `mpv` fork()ed from the long-running tracker fails ALSA card
enumeration ("cannot get card index"), even when the same command plays
fine from a shell. A timer-fired chime runs in a clean PID1-spawned
service where the audio device opens normally.

## Install

```bash
REPO_DIR=/home/robk/plane-tracker-rgb-pi   # adjust to your checkout
sudo apt install -y mpv
for f in flight-tracker-chime.service flight-tracker-chime.timer; do
  sed "s|__REPO_DIR__|$REPO_DIR|g" "$REPO_DIR/its-a-plane-python/setup/systemd/$f" \
    | sudo tee /etc/systemd/system/$f > /dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now flight-tracker-chime.timer
```

## Configure

In `/etc/plane-tracker.env` (or `.env` for local dev):

```
HOURLY_CHIME_ENABLED=True
HOURLY_CHIME_VOLUME=50          # mpv volume 0-100
HOURLY_CHIME_QUIET_START=22:00  # no chime in this window
HOURLY_CHIME_QUIET_END=08:00    # (overnight windows supported)
```

`fire_once()` re-reads config each run, so changes take effect on the next
hour without restarting anything. Test it immediately with:

```bash
sudo systemctl start flight-tracker-chime.service
journalctl -u flight-tracker-chime.service -n 5
```

The player prefers a USB audio card if present (and an ALSA `usbmix` dmix
PCM if configured, so the chime can mix over other audio), falling back to
the onboard output.
