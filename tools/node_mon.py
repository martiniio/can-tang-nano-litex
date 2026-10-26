#!/usr/bin/env python3
"""
node_mon.py -- the camera node over the UART: timestamped text, pictures saved, keys sent back.

    python3 node_mon.py /dev/ttyS1            # 3 Mbaud, pictures into out/
    python3 node_mon.py /dev/ttyS1 --quiet    # hide the 1 s "cam ..." status lines

    19:33:05.313  PIC picture=7 rows=600 bytes=812
    19:33:07.240  PIC_END picture=7 frames=4381..4450
    19:33:07.250  picture 7 -> out/pic7.png  rows 600/600  crc_bad 0  bad_hdr 0  dup 0
                      1.93 s  0.25 MB/s  frames 4381..4450

A picture is "PIC ..." followed by the binary row packets (see firmware/main.c, capture.h).
Every row's CRC (made in the fabric) is checked here: end to end over fabric, M3 and UART.

Keys typed here, then Enter, go to the node: s = picture, t = drain test.
Setup is automatic: other programs holding the UART (a forgotten microcom, cam_rx, ...) are
stopped first, because two readers split the data between them.
"""
import argparse
import binascii
import os
import re
import select
import signal
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial is missing: pip install pyserial (or opkg/apt install python3-serial)")

try:
    from PIL import Image
except ImportError:
    Image = None                                  # no PIL: plain .pgm


def ts():
    t = time.time()
    return time.strftime("%H:%M:%S", time.localtime(t)) + ".%03d" % (int(t * 1000) % 1000)


def say(text):
    print("{}  {}".format(ts(), text), flush=True)


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
                    say("setup: stopping {} (pid {}), it has {} open".format(name, pid, port))
                    os.kill(int(pid), signal.SIGTERM)
                    break
            except OSError:
                pass


# Picture: row packets -> image ----------------------------------------------------------------------

def assemble(data, rows, size):
    """Row packets -> (width, pixels, stats). Packet layout: firmware/capture.h."""
    width = size - 12
    pixels = bytearray(width * rows)
    got = set()
    st = {"bad_hdr": 0, "crc_bad": 0, "dup": 0, "frames": []}
    for i in range(len(data) // size):
        p = data[i * size:(i + 1) * size]
        y = p[2] | p[3] << 8
        if p[0:2] != b"RW" or y >= rows:
            st["bad_hdr"] += 1
            continue
        row = p[8:8 + width]
        if binascii.crc_hqx(row, 0xFFFF) != (p[-4] | p[-3] << 8):
            st["crc_bad"] += 1
            continue
        if y in got:
            st["dup"] += 1
            continue
        got.add(y)
        st["frames"].append(p[4] | p[5] << 8)
        pixels[y * width:(y + 1) * width] = row
    st["rows"] = len(got)
    return width, pixels, st


def save_picture(path, width, rows, pixels):
    if Image:
        Image.frombytes("L", (width, rows), bytes(pixels)).save(path + ".png")
        return path + ".png"
    with open(path + ".pgm", "wb") as f:
        f.write(b"P5 %d %d 255\n" % (width, rows) + bytes(pixels))
    return path + ".pgm"


# Main ------------------------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", help="UART device, e.g. /dev/ttyS1")
    ap.add_argument("-b", "--baud", type=int, default=3000000)
    ap.add_argument("--quiet", action="store_true", help="hide the 1 s 'cam ...' status lines")
    ap.add_argument("--out", default="out", help="folder for the pictures (default: out)")
    a = ap.parse_args()

    if not os.path.exists(a.port):
        sys.exit("{} does not exist".format(a.port))
    os.makedirs(a.out, exist_ok=True)
    free_port(a.port)
    time.sleep(0.2)
    uart = serial.Serial(a.port, a.baud, timeout=0)
    uart.reset_input_buffer()
    say("listening on {} at {} baud{} -- keys: s / t then Enter, Ctrl+C quits".format(
        a.port, a.baud, " (status lines hidden)" if a.quiet else ""))

    buf = b""
    pic = None          # picture being received: dict(id, rows, size, need, t0, skip_cr)
    t_rx = time.time()  # last time bytes arrived

    def finish(p, data):
        width, pixels, st = assemble(data, p["rows"], p["size"])
        dt = time.time() - p["t0"]
        fr = st["frames"]
        span = "frames {}..{}".format(fr[0], fr[-1]) if fr else "no rows"
        stats = "rows {}/{}  crc_bad {}  bad_hdr {}  dup {}".format(
            st["rows"], p["rows"], st["crc_bad"], st["bad_hdr"], st["dup"])
        path = save_picture(os.path.join(a.out, "pic{}".format(p["id"])), width, p["rows"], pixels)
        say("picture {} -> {}  {}".format(p["id"], path, stats))
        say("    {:.2f} s  {:.2f} MB/s  {}{}".format(
            dt, len(data) / dt / 1e6 if dt else 0, span,
            "" if len(data) == p["need"] else "  INCOMPLETE: {} of {} bytes".format(len(data), p["need"])))

    while True:
        ready, _, _ = select.select([uart.fileno(), sys.stdin.fileno()], [], [], 0.5)
        if uart.fileno() in ready:
            chunk = uart.read(65536)
            if chunk:
                buf += chunk
                t_rx = time.time()

        while True:
            if pic:                                               # binary: the row packets
                if pic["skip_cr"] and buf:                        # LiteX printf ends lines "\n\r":
                    if buf[:1] == b"\r":                          # the "\r" of the PIC line comes
                        buf = buf[1:]                             # before the first packet
                    pic["skip_cr"] = False
                if len(buf) < pic["need"]:
                    if time.time() - t_rx > 2.0:                  # stream stopped: keep what came
                        n = len(buf) - len(buf) % pic["size"]
                        finish(pic, buf[:n])
                        buf, pic = b"", None
                    break
                finish(pic, buf[:pic["need"]])
                buf, pic = buf[pic["need"]:], None
                continue
            if b"\n" not in buf:                                  # text: one line at a time
                break
            raw, buf = buf.split(b"\n", 1)
            text = "".join(chr(c) if 32 <= c < 127 else "." for c in raw if c != 13).strip()
            if not text or (a.quiet and text.startswith("cam t=")):
                continue
            say(text)
            m = re.match(r"PIC picture=(\d+) rows=(\d+) bytes=(\d+)", text)
            if m:
                rows, size = int(m.group(2)), int(m.group(3))
                pic = dict(id=int(m.group(1)), rows=rows, size=size, need=rows * size,
                           t0=time.time(), skip_cr=True)

        if sys.stdin.fileno() in ready:
            keys = os.read(sys.stdin.fileno(), 64).replace(b"\n", b"")
            if keys:
                uart.write(keys)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
