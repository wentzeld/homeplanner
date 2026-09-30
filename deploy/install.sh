#!/bin/bash
# One-time setup of HomePlanner on a Raspberry Pi 5 (Raspberry Pi OS, 64-bit, with desktop).
# Run as your normal desktop user (not root), from anywhere:  ./deploy/install.sh
# Safe to run again after updating the code or config.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
step() { printf '\n==> %s\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your normal user (it uses sudo where needed), not as root." >&2
  exit 1
fi

step "Installing system packages"
sudo apt-get update
sudo apt-get install -y python3-venv python3-dev build-essential curl wlr-randr fonts-noto-color-emoji
if ! command -v chromium >/dev/null && ! command -v chromium-browser >/dev/null; then
  sudo apt-get install -y chromium || sudo apt-get install -y chromium-browser
fi

step "Creating Python environment"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[pi]"

step "Checking config.toml"
if [ ! -f config.toml ]; then
  cp config.example.toml config.toml
  echo "Created config.toml from the example. Edit it (calendar_id, members, location,"
  echo "service_account_file), then run this script again."
  exit 1
fi
.venv/bin/python - <<'PY'
from homeplanner.config import load_config
c = load_config("config.toml")
print(f"  calendar: {c.calendar_id}")
print(f"  members:  {', '.join(m.name for m in c.members)}")
print(f"  screen off {c.sleep.off:%H:%M}-{c.sleep.on:%H:%M} on output {c.sleep.output}")
if not c.service_account_file.exists():
    print(f"  WARNING: service account key not found at {c.service_account_file}")
    print("           The display will show 'Calendar not connected' until it is there.")
PY

step "Configuring the desktop: auto-login, Wayland (labwc), no screen blanking"
if command -v raspi-config >/dev/null; then
  sudo raspi-config nonint do_boot_behaviour B4   # boot to desktop, logged in automatically
  sudo raspi-config nonint do_wayland W3 || true  # labwc compositor (default on current Pi OS)
  sudo raspi-config nonint do_blanking 1 || true  # never blank the screen; HomePlanner handles sleep
else
  echo "  raspi-config not found; set desktop auto-login and disable screen blanking manually."
fi

step "Allowing key presses to wake the screen (input group)"
sudo usermod -aG input "$USER"

step "Installing the backend service"
mkdir -p ~/.config/systemd/user
sed "s|@APP_DIR@|$APP_DIR|g" deploy/homeplanner.service > ~/.config/systemd/user/homeplanner.service
sudo loginctl enable-linger "$USER"  # start the service at boot, before anyone logs in
systemctl --user daemon-reload
systemctl --user enable homeplanner.service
systemctl --user restart homeplanner.service

step "Starting the calendar full-screen when the desktop starts"
chmod +x deploy/kiosk.sh
AUTOSTART=~/.config/labwc/autostart
mkdir -p "$(dirname "$AUTOSTART")"
if [ -f "$AUTOSTART" ] && ! grep -q "HomePlanner" "$AUTOSTART"; then
  cp "$AUTOSTART" "$AUTOSTART.before-homeplanner"
  echo "  Backed up your existing autostart to $AUTOSTART.before-homeplanner"
fi
cat > "$AUTOSTART" <<AUTO
# HomePlanner kiosk (installed by deploy/install.sh; undo with deploy/uninstall.sh)
$APP_DIR/deploy/kiosk.sh &
AUTO

step "Done"
echo "Reboot to start the calendar:  sudo reboot"
echo "Logs:  journalctl --user -u homeplanner -f"
