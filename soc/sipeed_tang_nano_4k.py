#
# This file is part of LiteX-Boards.
#
# Copyright (c) 2021 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

# Board diagram/pinout:
# http://www.fabienm.eu/flf/wp-content/uploads/2021/08/Tang-Nano-4K-specifications.jpg
# http://www.fabienm.eu/flf/wp-content/uploads/2021/08/Tang-Nano-4K-GW1NSR-4C-FPGA-board-pinout-diagram.jpg


from migen import *

from litex.build.generic_platform import *
from litex.build.gowin.platform import GowinPlatform
from litex.build.gowin.programmer import GowinProgrammer
from litex.build.openfpgaloader import OpenFPGALoader

# IOs ----------------------------------------------------------------------------------------------

_io = [
    # Clk / Rst
    ("clk27",  0, Pins("45"), IOStandard("LVCMOS33")),

    # Leds
    ("user_led", 0, Pins("10"), IOStandard("LVCMOS33")),

    # Buttons.
    ("user_btn_n", 0, Pins("14"), IOStandard("LVCMOS18")),
    ("user_btn_n", 1, Pins("15"), IOStandard("LVCMOS18")),

    # Serial -- moved off the DVP connector (was 39/40)
    ("serial", 0,
        Subsignal("rx", Pins("9"), Misc("PULL_MODE=UP")),
        Subsignal("tx", Pins("8")),
        IOStandard("LVCMOS33")
    ),
    # CAN (SN65HVD230)
    ("can", 0,
        Subsignal("tx", Pins("48")),
        Subsignal("rx", Pins("1"), Misc("PULL_MODE=UP")),
        IOStandard("LVCMOS33")
    ),

    # Onboard SPI flash CS, held high so CAN on pin 1 can't clock the flash
    ("flash_cs_n", 0, Pins("2"), IOStandard("LVCMOS33")),
    # OV2640 DVP camera connector -- pins from Sipeed camera_hdmi/src/dk_video.cst
    ("dvp_camera", 0,
        Subsignal("xclk",  Pins("33"), IOStandard("LVCMOS25"), Misc("DRIVE=8")),
        Subsignal("scl",   Pins("44"), IOStandard("LVCMOS33"), Misc("DRIVE=8")),
        Subsignal("sda",   Pins("46"), IOStandard("LVCMOS33"), Misc("DRIVE=8")),
        Subsignal("pclk",  Pins("41"), IOStandard("LVCMOS33"), Misc("PULL_MODE=UP")),
        Subsignal("href",  Pins("42"), IOStandard("LVCMOS33"), Misc("PULL_MODE=UP")),
        Subsignal("vsync", Pins("43"), IOStandard("LVCMOS33"), Misc("PULL_MODE=UP")),
        #                      D0 D1 D2 D3 D4 D5 D6 D7  (bank 3, 1.8 V)
        Subsignal("data_lo", Pins("22 21 17 19 20 18 16 23"), IOStandard("LVCMOS18"), Misc("PULL_MODE=UP")),
        #                      D8 D9                    (bank 1, 3.3 V)
        Subsignal("data_hi", Pins("39 40"), IOStandard("LVCMOS33"), Misc("PULL_MODE=UP")),
    ),
    # SPIFlash
    # SPIFlash
    ("spiflash", 0,
        Subsignal("cs_n", Pins("2"),  IOStandard("LVCMOS33")),
        Subsignal("clk",  Pins("1"),  IOStandard("LVCMOS33")),
        Subsignal("miso", Pins("47"), IOStandard("LVCMOS33")),
        Subsignal("mosi", Pins("48"), IOStandard("LVCMOS33")),
        Subsignal("wp",   Pins("8"),  IOStandard("LVCMOS33")),
        Subsignal("hold", Pins("9"),  IOStandard("LVCMOS33")),
    ),
    ("spiflash4x", 0,
        Subsignal("cs_n", Pins("2")),
        Subsignal("clk",  Pins("1")),
        Subsignal("dq",   Pins("48 47 8 9")),
        IOStandard("LVCMOS33")
    ),

    # HyperRAM (embedded in SIP, requires specific IO naming).
    ("O_hpram_ck",      0, Pins(1)),
    ("O_hpram_ck_n",    0, Pins(1)),
    ("O_hpram_cs_n",    0, Pins(1)),
    ("O_hpram_reset_n", 0, Pins(1)),
    ("IO_hpram_dq",     0, Pins(8)),
    ("IO_hpram_rwds",   0, Pins(1)),

    # HDMI.
    ("hdmi", 0,
        Subsignal("clk_p",   Pins("28")),
        Subsignal("clk_n",   Pins("27")),
        Subsignal("data0_p", Pins("30")),
        Subsignal("data0_n", Pins("29")),
        Subsignal("data1_p", Pins("32")),
        Subsignal("data1_n", Pins("31")),
        Subsignal("data2_p", Pins("35")),
        Subsignal("data2_n", Pins("34")),
        Misc("PULL_MODE=NONE"),
    ),
]

# Connectors ---------------------------------------------------------------------------------------

_connectors = [
    ["P6", "30 29 28 27 23 22 21 20 19 18 17 13 16  9  8 33  2  -  -  -  - 15"],
    ["P7", "31 32 34 35 10 39 40 41 42 43 47 48  1 46 44  -  -  6  3  4  7 14"],
]

# Platform -----------------------------------------------------------------------------------------

class Platform(GowinPlatform):
    default_clk_name   = "clk27"
    default_clk_period = 1e9/27e6

    def __init__(self, toolchain="gowin"):
        # Educational GoWin IDE v. 1.9.8.03
        GowinPlatform.__init__(self, "GW1NSR-LV4CQN48PC6/I5", _io, _connectors, toolchain=toolchain, devicename="GW1NSR-4C")
        # Licensed GoWin IDE v. 1.9.7.06 Beta
        # GowinPlatform.__init__(self, "GW1NSR-LV4CQN48PC7/I6", _io, _connectors, toolchain=toolchain, devicename="GW1NSR-4C")
        self.toolchain.options["use_mode_as_gpio"] = 1
        self.toolchain.options["use_mspi_as_gpio"] = 1
        self.toolchain.options["use_done_as_gpio"] = 1

    def create_programmer(self, kit="openfpgaloader"):
        if kit == "gowin":
            return GowinProgrammer(self.devicename)
        elif kit == "openfpgaloader":
            return OpenFPGALoader("tangnano4k")
        else:
            raise ValueError(f"Unsupported programmer kit: {kit}")

    def do_finalize(self, fragment):
        GowinPlatform.do_finalize(self, fragment)
        self.add_period_constraint(self.lookup_request("clk27", loose=True), 1e9/27e6)
