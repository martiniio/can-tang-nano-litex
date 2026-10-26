#!/bin/sh
# Copy everything Gowin needs into Google Drive, with relative paths in run.tcl,
# plus the M3 firmware and a flash.sh to build + program from MSYS2 on Windows.
# Usage: scripts/to_gowin.sh <step-name> [firmware.bin]
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
STEP=${1:?usage: to_gowin.sh <step-name> [firmware.bin]}
FW=${2:-$ROOT/firmware/firmware.bin}
DEST="$HOME/shared-drives/G:/My Drive/tang+litex/$STEP"
B="$ROOT/build/sipeed_tang_nano_4k/gateware"
mkdir -p "$DEST"

# gateware
cp "$B/sipeed_tang_nano_4k.v" "$B/sipeed_tang_nano_4k.cst" "$B/sipeed_tang_nano_4k.sdc" \
   "$B/sipeed_tang_nano_4k_mem.init" "$B/run.tcl" "$DEST"/
# every extra VHDL/Verilog file listed in run.tcl
grep -E '^add_file .*\.(vhd|v)$' "$B/run.tcl" | awk '{print $2}' | xargs -I{} cp {} "$DEST"/
# strip directories: add_file /long/path/x.vhd -> add_file x.vhd
sed -i 's#add_file .*/\([^/]*\)$#add_file \1#' "$DEST/run.tcl"

# M3 firmware
cp "$FW" "$DEST/firmware.bin"
cp "$ROOT/scripts/flash.sh" "$DEST/" && chmod +x "$DEST/flash.sh"

echo "== $DEST"; ls "$DEST"; echo "== run.tcl"; cat "$DEST/run.tcl"
