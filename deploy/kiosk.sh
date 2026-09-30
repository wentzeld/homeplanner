#!/bin/bash
# Starts Chromium full-screen on the HomePlanner page, and restarts it if it ever exits.
# Launched from the desktop's autostart (see install.sh).
URL="http://127.0.0.1:8000"

# Wait (up to 2 min) for the backend so the first page load doesn't fail.
for _ in $(seq 1 120); do
  curl -sf "$URL/api/status" >/dev/null && break
  sleep 1
done

BROWSER="$(command -v chromium || command -v chromium-browser)"
if [ -z "$BROWSER" ]; then
  echo "HomePlanner kiosk: Chromium not found" >&2
  exit 1
fi

while true; do
  "$BROWSER" \
    --kiosk \
    --incognito \
    --noerrdialogs \
    --disable-infobars \
    --no-first-run \
    --disable-session-crashed-bubble \
    --disable-features=Translate \
    --password-store=basic \
    --check-for-update-interval=31536000 \
    --ozone-platform=wayland \
    "$URL"
  sleep 3
done
