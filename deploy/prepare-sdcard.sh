#!/bin/bash
# Run on your Mac with the Pi's SD card inserted (after Raspberry Pi Imager has written it).
# Copies HomePlanner, your config.toml and the Google key onto the card's boot partition.
# Then, on the Pi, run:  bash /boot/firmware/homeplanner/deploy/setup-from-sdcard.sh
#
# Usage: ./deploy/prepare-sdcard.sh [--key PATH] [--volume PATH] [--no-eject]
#   --key PATH     Google service account key (default: ~/Downloads/service-account.json if present)
#   --volume PATH  the card's boot partition (default: /Volumes/bootfs)
#   --no-eject     leave the card mounted afterwards
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VOLUME="/Volumes/bootfs"
KEY=""
EJECT=1
step() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --key) KEY="${2:?--key needs a path}"; shift 2 ;;
    --volume) VOLUME="${2:?--volume needs a path}"; shift 2 ;;
    --no-eject) EJECT=0; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
done

step "Checking the SD card"
[ -d "$VOLUME" ] || die "$VOLUME not found. Insert the SD card (re-insert it after Imager ejects it)."
[ -f "$VOLUME/config.txt" ] || die "$VOLUME doesn't look like a Raspberry Pi boot partition (no config.txt)."
echo "  found $VOLUME"

step "Checking config.toml"
CONFIG="$APP_DIR/config.toml"
[ -f "$CONFIG" ] || die "config.toml not found. Create it first:
  cp config.example.toml config.toml && open -e config.toml"
if [ -x "$APP_DIR/.venv/bin/python" ]; then
  "$APP_DIR/.venv/bin/python" - "$CONFIG" <<'PY' || die "config.toml has errors (see above). Fix them and run this again."
import sys
from pydantic import ValidationError
from homeplanner.config import load_config
try:
    c = load_config(sys.argv[1])
except ValidationError as e:
    print(e, file=sys.stderr)
    sys.exit(1)
print(f"  calendar: {c.calendar_id}")
print(f"  members:  {', '.join(m.name for m in c.members)}")
import tomllib
raw = tomllib.load(open(sys.argv[1], "rb")).get("service_account_file")
if raw is None:
    print("  key path: default (~/.config/homeplanner/service-account.json on the Pi)")
elif "/home/pi/" in raw:
    print(f"  WARNING: service_account_file ({raw}) uses the user 'pi'.")
    print("           Make sure that's your Pi username, or delete the line to use the default.")
elif not (raw.startswith("~/") or raw.startswith("/home/")):
    print(f"  WARNING: service_account_file is {raw}")
    print("           It should be a path on the Pi. Delete the line to use the default.")
PY
else
  echo "  WARNING: no .venv here, so config.toml can't be checked. It will be checked on the Pi."
fi

step "Finding the Google key"
if [ -z "$KEY" ] && [ -f "$HOME/Downloads/service-account.json" ]; then
  KEY="$HOME/Downloads/service-account.json"
fi
if [ -n "$KEY" ]; then
  [ -f "$KEY" ] || die "key file not found: $KEY"
  echo "  using $KEY"
else
  echo "  no key given (fine if the Pi already has one; otherwise pass --key PATH)"
fi

step "Copying HomePlanner to the card"
STAGE="$VOLUME/homeplanner"
rm -rf "$STAGE" "$VOLUME/homeplanner-key.json"
mkdir -p "$STAGE"
# -rt: FAT32 has no permissions/owners. COPYFILE_DISABLE stops macOS adding ._ files.
COPYFILE_DISABLE=1 rsync -rt \
  --exclude .venv --exclude cache --exclude /data --exclude __pycache__ --exclude .pytest_cache \
  --exclude .git --exclude .env --exclude .DS_Store --exclude '._*' --exclude '*service-account*.json' \
  "$APP_DIR/" "$STAGE/"
find "$STAGE" -name '._*' -delete
rm -f "$VOLUME/._homeplanner"  # macOS metadata for the folder itself
if [ -n "$KEY" ]; then
  COPYFILE_DISABLE=1 cp "$KEY" "$VOLUME/homeplanner-key.json"
  rm -f "$VOLUME/._homeplanner-key.json"
fi
echo "  copied $(find "$STAGE" -type f | wc -l | tr -d ' ') files"

if [ "$EJECT" -eq 1 ]; then
  step "Ejecting the card"
  sync
  diskutil eject "$VOLUME"
fi

step "Done"
cat <<EOF
Put the card in the Pi and start it. Once the desktop is up, open Terminal and run:

  bash /boot/firmware/homeplanner/deploy/setup-from-sdcard.sh

(The Pi needs internet for this step.)
EOF
