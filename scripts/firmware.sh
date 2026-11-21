#!/usr/bin/env bash
set -euo pipefail
CLIK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CLIK_PROFILE=${1:-cores3}
if [ "$#" -gt 0 ]; then shift; fi
case "$CLIK_PROFILE" in
  cores3|lcd154|lcd154-touch|cores3-network|lcd154-network|lcd154-touch-network|cores3-hid|lcd154-hid|lcd154-touch-hid) ;;
  *) printf '%s\n' 'Profile: cores3, lcd154 or lcd154-touch, optionally suffixed -network or -hid.' >&2; exit 2 ;;
esac
if ! command -v idf.py >/dev/null 2>&1; then
  printf '%s\n' 'Load ESP-IDF v5.4.2 export.sh before running this script.' >&2
  exit 2
fi
if ! idf.py --version | grep -q 'v5\.4\.2'; then
  printf '%s\n' 'This release requires ESP-IDF v5.4.2.' >&2
  exit 2
fi
if [ "$#" -eq 0 ]; then set -- build; fi
exec idf.py -C "$CLIK_ROOT/firmware" \
  -B "$CLIK_ROOT/firmware/build-$CLIK_PROFILE" \
  -D "IDF_TARGET=esp32s3" \
  -D "SDKCONFIG=$CLIK_ROOT/firmware/sdkconfig.$CLIK_PROFILE" \
  -D "SDKCONFIG_DEFAULTS=$CLIK_ROOT/firmware/sdkconfig.defaults;$CLIK_ROOT/firmware/boards/$CLIK_PROFILE.defaults" \
  "$@"
