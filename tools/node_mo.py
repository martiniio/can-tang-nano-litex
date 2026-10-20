#!/usr/bin/env python3
"""
node_mon.py -- watch the vision node over the UART: timestamped text, keys sent back.

    python3 node_mon.py /dev/ttyS1            # 3 Mbaud
    python3 node_mon.py /dev/ttyS1 --quiet    # hide the 200 ms "w ..." status lines

    19:33:02.104  w t=12.4s f=4210 fps=35 cal=0 act=0 rdy=0 lost=0 chg=2/192
    19:33:05.312  EVENT frame=4380 tiles=5 bbox x6-8 y3-5
    19:33:05.790  snitch: picture=1 rows=600/600 dup=0 bad_hdr=0 bad_crc=0 frames=4381..4392 OK

Setup is automatic: other programs holding the UART (a forgotten microcom, cam_rx, ...) are
stopped first, because two readers split the data between them.
Keys typed here (s, r, then Enter) are sent to the node.
"""
import argparse
import os
import select
import signal
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial is missing: pip install pyserial (or opkg/apt install python3-serial)")


def ts():
    t = time.time()
    return time.strftime("%H:%M:%S", time.localtime(t)) + ".%03d" % (int(t * 1000) % 1000)


def free_port(port):
    """Stop other processes that have the UART open (they would steal bytes)."""
    real = os.path.realpath(port)
    me = os.getpid()
    for pid in filter(str.isdigit, os.listdir("/proc")):
        if int(pid) == me:
            continue
        try:
            fds = os.listdir("/proc/{}/fd".format(pid))
        except OSError:
            continue
        for fd in fds:
            try:
                if os.path.realpath("/proc/{}/fd/{}".format(pid, fd)) == real:
                    with open("/proc/{}/comm".format(pid)) as f:
                        name = f.read().strip()
                    print("{}  setup: stopping {} (pid {}), it has {} open".format(ts(), name, pid, port))
                    os.kill(int(pid), signal.SIGTERM)
                    break
            except OSError:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", help="UART device, e.g. /dev/ttyS1")
    ap.add_argument("-b", "--baud", type=int, default=3000000)
    ap.add_argument("--quiet", action="store_true", help="hide the 200 ms 'w ...' status lines")
    a = ap.parse_args()

    if not os.path.exists(a.port):
        sys.exit("{} does not exist".format(a.port))
    free_port(a.port)
    time.sleep(0.2)
    uart = serial.Serial(a.port, a.baud, timeout=0)
    uart.reset_input_buffer()

    line = b""
    while True:
        ready, _, _ = select.select([uart.fileno(), sys.stdin.fileno()], [], [], 1.0)
        if uart.fileno() in ready:
            line += uart.read(4096)
            *lines, line = line.split(b"\n")
            for l in lines:
                text = "".join(chr(c) if 32 <= c < 127 else "." for c in l if c != 13).strip()
                if text and not (a.quiet and text.startswith("w t=")):
                    print("{}  {}".format(ts(), text), flush=True)
        if sys.stdin.fileno() in ready:
            keys = os.read(sys.stdin.fileno(), 64).replace(b"\n", b"")
            if keys:
                uart.write(keys)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
