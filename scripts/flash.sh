#!/usr/bin/env bash
# flash.sh -- Gowin build + summary + flash, run on the Windows host (MSYS) in a step folder.
#
#   ./flash.sh           build, show summary, flash (bitstream + firmware.bin)
#   ./flash.sh build     build + summary only
#   ./flash.sh flash     flash what is already built (no rebuild)
#   ./flash.sh report    summary of the last build only
#
# gw_sh has no command-line flags (-V/--help are ignored); its verbosity is set with set_option in
# run.tcl, which this script adds once. Full tool output goes to gw.log.
set -uo pipefail
cd "$(dirname "$0")"

GW_SH=${GW_SH:-/c/Gowin/Gowin_V1.9.12.01_x64/IDE/bin/gw_sh.exe}
FS=impl/pnr/project.fs
RPT=impl/pnr/project.rpt.txt
LOG=gw.log
MODE=${1:-all}

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
hdr()   { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# Verbosity options, inserted once before "run all" -----------------------------------------------
tune_tcl() {
    for opt in "print_all_synthesis_warning 1" "show_all_warn 1" "gen_text_timing_rpt 1"; do
        grep -q "set_option -${opt%% *} " run.tcl ||
            sed -i "s/^run all/set_option -$opt\nrun all/" run.tcl
    done
}

# Summary of the last build -----------------------------------------------------------------------
report() {
    if [ -f "$LOG" ]; then
        hdr "messages ($LOG)"
        echo "errors: $(grep -c 'ERROR' "$LOG")   warnings: $(grep -c 'WARN' "$LOG")"
        grep 'ERROR' "$LOG" | sort -u | head -20
        # most frequent warning codes, e.g. (EX3780) or (PA1001)
        grep 'WARN' "$LOG" | grep -o '([A-Z]\+[0-9]\+)' | sort | uniq -c | sort -rn | head -8
    fi

    hdr "resources ($RPT)"
    if [ -f "$RPT" ]; then
        awk '/Resource Usage Summary/{p=1} p&&/^[0-9]+\. /&&!/Resource Usage/{exit} p' "$RPT" \
            | grep -v '^\s*$' | grep -v -- '------'
    else
        echo "(no report: place & route did not run)"
    fi

    hdr "timing"
    local tr
    tr=$(ls impl/pnr/*.tr* 2>/dev/null | grep -v '\.html$' | head -1)
    if [ -n "$tr" ]; then
        # The report starts with a table of contents; the real sections are the LAST matches.
        local n
        for sec in 'Max Frequency Summary' 'Total Negative Slack Summary'; do
            n=$(grep -n "$sec" "$tr" | tail -1 | cut -d: -f1)
            [ -n "$n" ] && awk -v n="$n" 'NR == n { print; next }
                NR > n { if ($0 ~ /^ *[0-9]+\.[0-9]/) exit; if (NF) print; if (NR > n + 20) exit }' "$tr"
        done
    else
        echo "(no text timing report)"
    fi
}

# Build -------------------------------------------------------------------------------------------
build() {
    [ -x "$GW_SH" ] || { red "gw_sh not found: $GW_SH (set GW_SH=...)"; exit 1; }
    tune_tcl
    rm -f "$FS"                                  # so a failed build can never flash an old .fs
    hdr "gw_sh run.tcl  (log: $LOG)"
    local t0=$SECONDS
    # gw_sh writes to the log file, NOT into a pipe: Gowin leaves a helper process running that
    # inherits the output handle, so a pipe (| tee) never sees EOF and the script hangs forever.
    # stdin from /dev/null so gw_sh can never sit at its interactive "%" prompt.
    "$GW_SH" run.tcl > "$LOG" 2>&1 < /dev/null &
    local pid=$!
    tail -n +1 -f --pid=$pid "$LOG" 2>/dev/null | grep --line-buffered -E 'ERROR|\[[0-9]+%\]|finish|completed$'
    wait $pid
    local rc=$?
    echo "gw_sh exit $rc, $((SECONDS - t0)) s"
    report
    if [ ! -f "$FS" ]; then
        red "BUILD FAILED (no $FS)"
        exit 1
    fi
    green "BUILD OK: $FS"
}

# Flash -------------------------------------------------------------------------------------------
flash() {
    [ -f "$FS" ]          || { red "no $FS: build first"; exit 1; }
    [ -f firmware.bin ]   || { red "no firmware.bin"; exit 1; }
    hdr "openFPGALoader: $FS + firmware.bin ($(stat -c %s firmware.bin) bytes)"
    openFPGALoader -b tangnano4k -f --mcufw firmware.bin "$FS" || { red "FLASH FAILED"; exit 1; }
    green "FLASHED"
}

case "$MODE" in
    all)    build; flash ;;
    build)  build ;;
    flash)  flash ;;
    report) report ;;
    *)      echo "usage: $0 [all|build|flash|report]"; exit 1 ;;
esac
