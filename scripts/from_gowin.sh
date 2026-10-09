#!/bin/sh
# Fetch project.fs from Google Drive and program the board.
# Usage: scripts/from_gowin.sh <step-name>
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
STEP=${1:?usage: from_gowin.sh <step-name>}
SRC="$HOME/shared-drives/G:/My Drive/tang+litex/$STEP"
cp "$SRC/project.fs" "$ROOT/"
cd "$ROOT"
sudo openFPGALoader -b tangnano4k -f \
    --mcufw build/sipeed_tang_nano_4k/software/bios/bios.bin \
    project.fs
