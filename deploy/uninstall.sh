#!/bin/bash
# Stops HomePlanner starting at boot and restores the normal desktop.
# Leaves the code, config.toml and the Python environment in place.
set -euo pipefail

systemctl --user disable --now homeplanner.service 2>/dev/null || true
rm -f ~/.config/systemd/user/homeplanner.service
systemctl --user daemon-reload

AUTOSTART=~/.config/labwc/autostart
if [ -f "$AUTOSTART.before-homeplanner" ]; then
  mv "$AUTOSTART.before-homeplanner" "$AUTOSTART"
elif [ -f "$AUTOSTART" ] && grep -q "HomePlanner" "$AUTOSTART"; then
  rm "$AUTOSTART"
fi

echo "HomePlanner removed from startup. Reboot to get the normal desktop back: sudo reboot"
