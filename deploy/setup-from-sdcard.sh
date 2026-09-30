#!/bin/bash
# Run on the Pi after deploy/prepare-sdcard.sh staged HomePlanner on the SD card:
#   bash /boot/firmware/homeplanner/deploy/setup-from-sdcard.sh
# Copies the code into ~/homeplanner, puts the Google key where config.toml says, removes the
# copies from the card, runs deploy/install.sh and offers to reboot. Safe to re-run for updates.
#
# For testing: HOMEPLANNER_SKIP_INSTALL=1 skips install.sh and the reboot question;
# SUDO= (empty) runs the privileged commands without sudo.
set -euo pipefail

# This script lives in the folder it deletes, so run from a temporary copy.
if [ -z "${HOMEPLANNER_STAGED_DIR:-}" ]; then
  STAGED="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  TMP_SCRIPT="$(mktemp)"
  cp "${BASH_SOURCE[0]}" "$TMP_SCRIPT"
  HOMEPLANNER_STAGED_DIR="$STAGED" exec bash "$TMP_SCRIPT" "$@"
fi
STAGED="$HOMEPLANNER_STAGED_DIR"
trap 'rm -f "${BASH_SOURCE[0]}"' EXIT

BOOT="$(dirname "$STAGED")"
STAGED_KEY="$BOOT/homeplanner-key.json"
DEST="$HOME/homeplanner"
SUDO="${SUDO-sudo}"
step() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "run this as your normal user (it uses sudo where needed), not as root."
[ -f "$STAGED/config.toml" ] || die "no config.toml in $STAGED. Run deploy/prepare-sdcard.sh on your Mac first."
[ "$STAGED" != "$DEST" ] || die "run the copy on the SD card: bash /boot/firmware/homeplanner/deploy/setup-from-sdcard.sh"

step "Copying HomePlanner to $DEST"
mkdir -p "$DEST"
# Keep the Python environment, saved events, and your calendars/PIN (data/) from any earlier install.
rsync -rt --delete --exclude .venv --exclude cache --exclude /data "$STAGED/" "$DEST/"
chmod +x "$DEST"/deploy/*.sh  # FAT32 on the card loses the executable bit

step "Installing the Google key"
# Same default and ~ expansion as homeplanner.config (DEFAULT_KEY_PATH); the app isn't installed yet.
KEY_PATH="$(python3 -c '
import os, sys, tomllib
raw = tomllib.load(open(sys.argv[1], "rb")).get("service_account_file", "~/.config/homeplanner/service-account.json")
print(os.path.expanduser(raw))' "$DEST/config.toml")"
if [ -f "$STAGED_KEY" ]; then
  case "$KEY_PATH" in
    "$HOME"/*) ;;
    *) die "service_account_file in config.toml is $KEY_PATH, which isn't in your home folder ($HOME).
Fix it on your Mac (or delete the line to use the default) and run prepare-sdcard.sh again." ;;
  esac
  install -d -m 700 "$(dirname "$KEY_PATH")"
  install -m 600 "$STAGED_KEY" "$KEY_PATH"
  echo "  installed at $KEY_PATH"
elif [ -f "$KEY_PATH" ]; then
  echo "  no new key on the card; keeping the existing one at $KEY_PATH"
else
  echo "  WARNING: no key on the card and none at $KEY_PATH."
  echo "           The display will say 'Calendar not connected' until the key is there."
fi

step "Removing the copies from the SD card"
$SUDO rm -rf "$STAGED" "$STAGED_KEY" "$BOOT/._homeplanner"
echo "  done"

if [ "${HOMEPLANNER_SKIP_INSTALL:-}" = "1" ]; then
  echo "HOMEPLANNER_SKIP_INSTALL=1: skipping install.sh"
  exit 0
fi

step "Running the installer"
echo "  (If something fails, fix it and run:  bash ~/homeplanner/deploy/install.sh)"
cd "$DEST"
bash deploy/install.sh

read -r -p $'\nReboot now to start the calendar? [Y/n] ' answer
case "$answer" in
  [nN]*) echo "OK. Reboot later with:  sudo reboot" ;;
  *) $SUDO reboot ;;
esac
