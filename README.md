# can-tang-nano-litex

A small LiteX SoC on the **Tang Nano 4K** (Gowin GW1NSR-4C) built around its hard Cortex-M3.

What is here today:

- **CAN**: the [CanLite](https://github.com/bggardner/can-lite-vhdl) controller as a LiteX module,
  500 kbit/s classic CAN. Tested with `canfdtest` from a Linux board (the Tang echoes every frame).
- **Camera**: an OV2640 front-end (800 × 600 greyscale, 34 fps) and **Capture**, which lets the M3
  read a full picture row by row and send it over UART.

## Layout

```
soc/        the SoC (tang4k.py) and the board's pins (sipeed_tang_nano_4k.py)
cores/      our LiteX modules: canlite.py, dvp_frontend.py, capture.py
rtl/        HDL used by the cores; third_party/ holds CanLite and Sipeed's OV2640 example (submodules)
firmware/   M3 firmware: one main.c, camera or CAN test chosen at build time
tools/      host side (Linux): node_mon.py receives pictures and shows the node's output
scripts/    flash.sh (Gowin build + summary + flash), to_gowin.sh
```

## Build and run

```bash
git clone --recursive https://github.com/martiniio/can-tang-nano-litex
cd can-tang-nano-litex

# 1. SoC: LiteX writes the Gowin project and the C headers
python3 -m soc.tang4k --cpu-type=gowin_emcu --with-camera --with-can \
                      --uart-baudrate 3000000 --build --no-compile-gateware

# 2. Firmware
cd firmware
make clean && make            # camera test
make clean && make APP=can    # CAN test

# 3. Bitstream + flash: copy build/.../gateware and firmware.bin to the machine with the
#    Gowin IDE, put scripts/flash.sh next to run.tcl, and run it there (gw_sh + openFPGALoader)
./flash.sh
```

**Camera test** (on the Linux side of the UART): `python3 tools/node_mon.py /dev/ttyS1`, then
`s` + Enter saves a picture to `out/`.

**CAN test** (Linux with can-utils):

```bash
ip link set can0 type can bitrate 500000 restart-ms 100 && ip link set can0 up
canfdtest -g -l 20000 can0
```

## Notes

- No interrupts reach the M3 from the fabric in this setup: the firmware polls.
- Write-up: [my blog](https://my-website-kohl-eta-22.vercel.app/)

## Third-party code

- [CanLite](https://github.com/bggardner/can-lite-vhdl), LGPL-2.1 (submodule)
- OV2640 SCCB controller and register table from
  [sipeed/TangNano-4K-example](https://github.com/sipeed/TangNano-4K-example) (submodule)
- [LiteX](https://github.com/enjoy-digital/litex)
