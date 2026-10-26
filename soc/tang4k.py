#!/usr/bin/env python3

#
# This file is part of LiteX-Boards.
#
# Copyright (c) 2021 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# Tang Nano 4K: Gowin EMCU (Cortex-M3) + optional camera (OV2640 -> DVPFrontEnd -> Capture)
# + optional CanLite (CAN).
# HyperRAM / HDMI video were removed: the M3 cannot reach HyperRAM through the EMCU's
# 64 KB fabric window, and nothing here uses HDMI.

import os

from migen import *

from litex.gen import *

from soc import sipeed_tang_nano_4k

from litex.soc.cores.clock.gowin_gw1n import GW1NPLL
from litex.soc.integration.soc import *
from litex.soc.integration.soc import SoCRegion
from litex.soc.integration.builder import *
from litex.soc.cores.led import LedChaser

from cansoc.canlite import CanLite
from cansoc.dvp_frontend import DVPFrontEnd
from cansoc.capture import Capture

# CAN bit timing ----------------------------------------------------------------------------------
# CanLite: bit = 2 * BRP * (1 + TSEG1 + TSEG2) clocks. With TSEG1=6, TSEG2=2 (9 tq, sample point
# 78 %) only BRP depends on the system clock: 27 MHz -> BRP 3, 54 MHz -> BRP 6 for 500 kbit/s.
CAN_BITRATE = 500e3
CAN_TQ      = 1 + 6 + 2

def can_brp(sys_clk_freq, bitrate=CAN_BITRATE):
    brp = sys_clk_freq / (2 * CAN_TQ * bitrate)
    assert abs(brp - round(brp)) < 1e-6, \
        "sys_clk_freq {:.0f} Hz gives no integer CAN BRP for {:.0f} bit/s".format(sys_clk_freq, bitrate)
    return int(round(brp))

# CRG ----------------------------------------------------------------------------------------------

class _CRG(LiteXModule):
    def __init__(self, platform, sys_clk_freq):
        self.rst    = Signal()
        self.cd_sys = ClockDomain()

        # # #

        # Clk / Rst
        clk27 = platform.request("clk27")
        rst_n = platform.request("user_btn_n", 0)

        # PLL
        self.pll = pll = GW1NPLL(devicename=platform.devicename, device=platform.device)
        self.comb += pll.reset.eq(~rst_n)
        pll.register_clkin(clk27, 27e6)
        pll.create_clkout(self.cd_sys, sys_clk_freq)

# BaseSoC ------------------------------------------------------------------------------------------

class BaseSoC(SoCCore):
    def __init__(self, toolchain="gowin", sys_clk_freq=54e6,
        with_led_chaser = True,
        with_camera     = False,
        cam_width       = 800,
        cam_height      = 600,
        with_can        = False,
        **kwargs):
        platform = sipeed_tang_nano_4k.Platform(toolchain=toolchain)

        # CRG --------------------------------------------------------------------------------------
        self.crg = _CRG(platform, sys_clk_freq)

        # SoCCore ----------------------------------------------------------------------------------
        if "cpu_type" in kwargs and kwargs["cpu_type"] == "gowin_emcu":
            kwargs["integrated_sram_size"] = 0     # SRAM is directly attached to CPU
            kwargs["integrated_rom_size"]  = 0     # boot flash directly attached to CPU
        else:
            # Disable Integrated ROM
            kwargs["integrated_rom_size"] = 0
        # ident="": no identifier ROM (it costs a whole BSRAM block, nothing here reads it).
        SoCCore.__init__(self, platform, sys_clk_freq, ident="", **kwargs)

        if self.cpu_type == 'vexriscv':
            assert self.cpu_variant == 'minimal', 'use --cpu-variant=minimal to fit into number of BSRAMs'

        # Gowin EMCU -------------------------------------------------------------------------------
        if self.cpu_type == "gowin_emcu":
            # Use EMCU's SRAM.
            self.bus.add_region("sram", SoCRegion(
                origin = self.cpu.mem_map["sram"],
                size   = 8 * KILOBYTE,
            ))
            # Use ECMU's FLASH as ROM.
            self.bus.add_region("rom", SoCRegion(
                origin = self.cpu.mem_map["rom"],
                size   = 32 * KILOBYTE,
                linker = True,
            ))
        # No Gowin EMCU ----------------------------------------------------------------------------
        else:
            # Use SPI-Flash as ROM.
            from litespi.modules import W25Q32
            from litespi.opcodes import SpiNorFlashOpCodes as Codes
            self.add_spi_flash(mode="1x", module=W25Q32(Codes.READ_1_1_1), with_master=False)
            self.bus.add_region("rom", SoCRegion(
                origin = self.bus.regions["spiflash"].origin,
                size   = 32 * KILOBYTE,
                linker = True)
            )
            self.cpu.set_reset_address(self.bus.regions["rom"].origin)

        # Leds -------------------------------------------------------------------------------------
        if with_led_chaser:
            self.leds = LedChaser(
                pads         = platform.request_all("user_led"),
                sys_clk_freq = sys_clk_freq)

        # CAN, 500 kbit/s (off by default: --with-can) --------------------------------------------
        if with_can:
            # fifo_depth=2: Gowin builds these FIFOs (101 / 133 bit wide) from flip-flops; 2 deep
            # instead of 4 saves ~470 FFs.
            self.can = CanLite(platform, platform.request("can"), sys_clk_freq, brp=can_brp(sys_clk_freq),
                fifo_depth=2)
        self.comb += platform.request("flash_cs_n").eq(1)   # keep onboard flash deselected (pins 1/48)

        # Camera: OV2640 -> DVPFrontEnd (pixel stream, CSRs fe_*) -> Capture (CSRs cap_*) ----------
        if with_camera:
            gw_dir = os.path.join("build", "sipeed_tang_nano_4k", "gateware")
            self.fe = DVPFrontEnd(platform, platform.request("dvp_camera"),
                width=cam_width, height=cam_height,
                sys_clk_freq=sys_clk_freq, xclk_freq=13.5e6, gateware_dir=gw_dir)
            self.cap = Capture(platform, self.fe.source,
                width=cam_width, height=cam_height, gateware_dir=gw_dir)
            for name, value in self.cap.constants.items():   # sizes -> soc.h
                self.add_constant(name, value)

# Gowin IDE fix ------------------------------------------------------------------------------------
# LiteX leaves the boot-flash data (FLASH256K.DOUT -> EMCU.TARGFLASH0HRDATA) unconnected because
# the open-source toolchain wires it internally. Gowin's synthesizer does not: it ties HRDATA to 0
# and the M3 reads only zeros from flash, so it never boots. Connect it explicitly.

def connect_emcu_flash_data(soc):
    for _, m in soc.cpu._submodules:
        if type(m).__name__ == "AHBFlash":
            dout = next(i.expr for i in m.flash.items if getattr(i, "name", None) == "DOUT")
            soc.cpu.cpu_params["i_TARGFLASH0HRDATA"] = dout
            return
    raise RuntimeError("AHBFlash not found")

# Build --------------------------------------------------------------------------------------------

def main():
    from litex.build.parser import LiteXArgumentParser
    parser = LiteXArgumentParser(platform=sipeed_tang_nano_4k.Platform, description="LiteX SoC on Tang Nano 4K.")
    parser.add_target_argument("--flash",        action="store_true",      help="Flash bitstream and BIOS.")
    parser.add_target_argument("--prog-kit",     default="openfpgaloader", help="Programmer select from Gowin/openFPGALoader.")
    parser.add_target_argument("--sys-clk-freq", default=54e6, type=float, help="System clock frequency.")
    parser.add_target_argument("--with-can",     action="store_true",      help="Add the CanLite CAN controller.")
    parser.add_target_argument("--with-camera",  action="store_true",      help="Add the camera: OV2640 + DVPFrontEnd + Capture.")
    parser.add_target_argument("--cam-width",    default=800, type=int,    help="Picture width  (multiple of 4, <= 800).")
    parser.add_target_argument("--cam-height",   default=600, type=int,    help="Picture height (multiple of 4, <= 600).")
    # Resource-saving defaults (the device is full): no SoC controller (reset/scratch/bus_errors
    # registers, unused by the firmware) and a 4-deep UART FIFO instead of 16.
    parser.set_defaults(no_ctrl=True, uart_fifo_depth=4)
    args = parser.parse_args()

    soc = BaseSoC(
        toolchain    = args.toolchain,
        sys_clk_freq = args.sys_clk_freq,
        with_camera  = args.with_camera,
        cam_width    = args.cam_width,
        cam_height   = args.cam_height,
        with_can     = args.with_can,
        **parser.soc_argdict
    )
    if soc.cpu_type == "gowin_emcu":
        connect_emcu_flash_data(soc)

    builder = Builder(soc, **parser.builder_argdict)
    if args.build:
        builder.build(**parser.toolchain_argdict)

    if args.load:
        prog = soc.platform.create_programmer(kit=args.prog_kit)
        prog.load_bitstream(builder.get_bitstream_filename(mode="sram"))

    if args.flash:
        prog               = soc.platform.create_programmer(kit=args.prog_kit)
        bitstream_filename = builder.get_bitstream_filename(mode="flash", ext=".fs") # FIXME
        bios_filename      = builder.get_bios_filename()
        if args.cpu_type != "gowin_emcu":
            prog.flash(address=0, data_file=bitstream_filename)
            prog.flash(address=0, data_file=bios_filename, external=True)
        else:
            prog.flash(
                address   = 0,
                data_file = bitstream_filename,
                mcufw     = bios_filename,
            )

if __name__ == "__main__":
    main()
