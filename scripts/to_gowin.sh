#!/bin/sh
# Copy everything Gowin needs into Google Drive, with relative paths in run.tcl.
# Usage: scripts/to_gowin.sh <step-name>
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
STEP=${1:?usage: to_gowin.sh <step-name>}
DEST="$HOME/shared-drives/G:/My Drive/tang+litex/$STEP"
B="$ROOT/build/sipeed_tang_nano_4k/gateware"
mkdir -p "$DEST"
cp "$B/sipeed_tang_nano_4k.v" "$B/sipeed_tang_nano_4k.cst" "$B/sipeed_tang_nano_4k.sdc" \
   "$B/sipeed_tang_nano_4k_mem.init" "$B/run.tcl" "$DEST"/
# every VHDL file listed in run.tcl
grep '^add_file .*\.vhd' "$B/run.tcl" | awk '{print $2}' | xargs -I{} cp {} "$DEST"/
# strip directories: add_file /long/path/x.vhd -> add_file x.vhd
sed -i 's#add_file .*/\([^/]*\)$#add_file \1#' "$DEST/run.tcl"
echo "== $DEST"; ls "$DEST"; echo "== run.tcl"; cat "$DEST/run.tcl"
