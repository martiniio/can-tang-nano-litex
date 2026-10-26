"""
dvp_frontend.py -- OV2640 DVP front-end: camera signals in, greyscale pixel stream out.

    sys 54 MHz --/4--> XCLK 13.5 MHz ---------------------------------------> OV2640
    sys 54 MHz ------> OV2640_Controller (Sipeed SCCB + register table) ----> SCL/SDA
                                                                                  |
    PCLK (~53.7 MHz, made by the sensor) <-- D[9:2], HREF, VSYNC <-----------------+
      |  input registers -> keep the Y byte of each YUV pair -> (pix, sol, sof)
      |
      +--> async FIFO (Migen AsyncFIFO, 8 x 10 bit)       <- the ONLY clock crossing
                |
    sys 54 MHz  v
      x / y / frame_id counters -> `source`  (valid, pix, x, y, sof, eol, eof, frame_id)

PCLK is made by the sensor's own clock, not locked to sys (measured 53.6-53.9 MHz), so it is
treated as fully asynchronous. In YUV one pixel = 2 PCLKs: ~27 Mpx/s into a reader that takes
one word per sys clock (54 M/s), so the FIFO never holds more than a few words.

The stream (`source`, sys domain)
---------------------------------
One pixel per cycle where valid=1; NO backpressure: consumers take every pixel.
    pix       8   Y value
    x, y     11   column 0..width-1, row 0..height-1
    sof           first pixel of a frame  (x=0, y=0)
    eol           last pixel of a row     (x=width-1)
    eof           last pixel of a frame   (x=width-1, y=height-1)
    frame_id 16   number of the frame this pixel belongs to (+1 at every sof)
Pixels beyond width/height are dropped, so x/y are always in range. Nothing is sent before the
first complete frame start.

Registers (CSR; the SoC adds the submodule name `fe` as prefix)
---------------------------------------------------------------
fe_status.cfg_done   SCCB init table finished (sensor configured)
                     The table is (re)sent automatically: 100 ms after reset, and again whenever
                     no frame arrives for 1 s (see the watchdog in the code).
fe_frame_id          frame_id of the stream right now (read it twice, 1 s apart -> fps)

The FIFO cannot overflow: the front-end itself drains it every sys clock (consumers cannot stall
the stream, they only watch it), and pixels arrive at most every 2nd PCLK.

Verified on hardware (DVPFrontEnd bring-up): 800x600, 34 fps, 480000 px / 600 rows per frame,
FIFO never full. The measurement registers used for that bring-up were removed afterwards.
"""

import os
import re

from migen import *
from migen.genlib.cdc import MultiReg
from migen.genlib.fifo import AsyncFIFO

from litex.gen import LiteXModule
from litex.soc.interconnect.csr import CSRStatus, CSRField

RTL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rtl", "ov2640")

# Pixel stream layout (sys domain), shared with Capture.
STREAM_LAYOUT = [
    ("valid",     1),
    ("pix",       8),
    ("x",        11),
    ("y",        11),
    ("sof",       1),
    ("eol",       1),
    ("eof",       1),
    ("frame_id", 16),
]


# Sensor register table -----------------------------------------------------------------------------

def patched_register_file(width, height, out_dir):
    """Copy Sipeed's OV2640_Registers.v with the output size patched and YUV output selected."""
    assert width % 4 == 0 and height % 4 == 0, "OV2640 output size must be a multiple of 4"
    assert width <= 800 and height <= 600, "the demo's sensor window is SVGA (800x600)"
    src = open(os.path.join(RTL_DIR, "OV2640_Registers.v")).read()

    # The active LUT is the first `case` block; a second, commented-out copy follows it.
    end = src.index("default :")
    head, tail = src[:end], src[end:]

    zmow, zmoh = width >> 2, height >> 2
    zmhh = ((zmoh >> 6) & 0x04) | ((zmow >> 8) & 0x03)   # 0x5C = {ZMOH[8], ZMOW[9:8]}
    for reg, val in [("5A", zmow & 0xFF), ("5B", zmoh & 0xFF), ("5C", zmhh)]:
        head, k = re.subn(r"(16'h{}_)[0-9A-Fa-f]{{2}}(;)".format(reg),
                          r"\g<1>{:02X}\2".format(val), head, count=1)
        assert k == 1, "register 0x{} not found in OV2640_Registers.v".format(reg)
    head, k = re.subn(r"(16'hDA_)[0-9A-Fa-f]{2}(;)", r"\g<1>00\2", head)   # 0xDA = 0x00: YUV422
    assert k >= 1, "register 0xDA not found in OV2640_Registers.v"

    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, "OV2640_Registers_{}x{}_yuv.v".format(width, height))
    with open(dst, "w") as f:
        f.write(head + tail)
    return dst


# Front-end -----------------------------------------------------------------------------------------

class DVPFrontEnd(LiteXModule):
    def __init__(self, platform, pads, width=800, height=600,
                 sys_clk_freq=54e6, xclk_freq=13.5e6,
                 gateware_dir="build/ov2640", sim=False):
        self.width, self.height = width, height
        self.source = source = Record(STREAM_LAYOUT)

        self.status = CSRStatus(fields=[
            CSRField("cfg_done", size=1, offset=0, description="SCCB init table finished."),
        ])
        self.frame_id = CSRStatus(16, description="Current frame_id (fps = difference over 1 s).")

        # XCLK = sys / xclk_div (even divider -> 50 % duty) ----------------------------------------
        # 13.5 MHz with CLKRC=0x80 (doubler) gives a 27 MHz sensor clock -> PCLK ~54 MHz in YUV.
        xclk_div = int(round(sys_clk_freq / xclk_freq))
        assert xclk_div >= 2 and xclk_div % 2 == 0, "xclk_div must be even"
        xclk     = Signal()
        xclk_cnt = Signal(max=xclk_div // 2)
        self.sync += If(xclk_cnt == xclk_div // 2 - 1,
            xclk_cnt.eq(0), xclk.eq(~xclk),
        ).Else(
            xclk_cnt.eq(xclk_cnt + 1),
        )
        self.comb += pads.xclk.eq(xclk)

        # SCCB init: Sipeed's controller + register table, with a start delay and a watchdog -------
        # Cold power-up: the FPGA boots in a few ms, the sensor needs longer, and a table sent
        # too early is lost (cfg_done=1 but no frames). So `resend` (holds the table at its
        # start) is kept high for the first 100 ms, and asserted again for 100 ms whenever no
        # frame has arrived for 1 s (camera not ready, glitch, re-plugged). One counter does both:
        #   wd = 0 .. hold-1          -> resend = 1 (waiting)
        #   wd = hold .. timeout-1    -> table runs / frames flow; every sof sets wd back to hold
        #   wd = timeout              -> no frame for ~1 s: wd = 0, start over
        hold    = int(sys_clk_freq * 0.1)
        timeout = int(sys_clk_freq * 1.0)
        wd      = Signal(max=timeout + 1)
        self.resend = resend = Signal()
        self.comb += resend.eq(wd < hold)
        self.sync += [
            If(wd == timeout, wd.eq(0)).Else(wd.eq(wd + 1)),
            If(source.sof & source.valid, wd.eq(hold)),
        ]

        cfg_done = Signal()
        if not sim:
            self.specials += Instance("OV2640_Controller",
                i_clk             = ClockSignal("sys"),
                i_resend          = resend,
                o_config_finished = cfg_done,
                o_sioc            = pads.scl,
                io_siod           = pads.sda,
                o_reset           = Signal(),
                o_pwdn            = Signal(),
            )
            platform.add_source(os.path.join(RTL_DIR, "OV2640_Controller.v"))
            platform.add_source(os.path.join(RTL_DIR, "I2C_Interface.v"))
            platform.add_source(patched_register_file(width, height, gateware_dir))
        self.specials += MultiReg(cfg_done, self.status.fields.cfg_done)

        # PCLK domain ------------------------------------------------------------------------------
        self.cd_pix = ClockDomain(reset_less=True)
        self.comb += self.cd_pix.clk.eq(pads.pclk)
        # Timing constraint only: PCLK = XCLK x2 (CLKRC doubler) x2 (YUV: 2 bytes per pixel).
        pclk_freq = 4 * xclk_freq
        if not sim:
            platform.add_period_constraint(pads.pclk, 1e9/pclk_freq)

        # D[9:0]: one bus, or data_lo (D0-D7) + data_hi (D8-D9) when the halves sit in banks with
        # different VCCIO (Tang Nano 4K: bank 3 = 1.8 V, bank 1 = 3.3 V).
        data = pads.data if hasattr(pads, "data") else Cat(pads.data_lo, pads.data_hi)
        d    = Signal(8)
        href = Signal()
        vs   = Signal()
        vs_d = Signal()
        self.sync.pix += [
            d.eq(data[2:10]),                # D[9:2]: the 8-bit YUV byte
            href.eq(pads.href),
            vs.eq(pads.vsync),
            vs_d.eq(vs),
        ]

        # Keep the first byte of each YUV pair (= Y, verified on hardware); phase restarts per line.
        byte_ph = Signal()
        take    = Signal()
        self.sync.pix += If(href, byte_ph.eq(~byte_ph)).Else(byte_ph.eq(0))
        self.comb += take.eq(href & ~byte_ph)

        # Mark the first kept pixel of each line (sol) and of each frame (sof).
        new_frame = Signal()                 # a VSYNC edge was seen, next line starts a frame
        first_px  = Signal()                 # next kept pixel is the first of its line
        sol = Signal()
        sof = Signal()
        self.comb += [
            sol.eq(take & first_px),
            sof.eq(take & first_px & new_frame),
        ]
        self.sync.pix += [
            If(vs ^ vs_d, new_frame.eq(1)).Elif(sof, new_frame.eq(0)),  # either VSYNC edge: both in blanking
            If(~href, first_px.eq(1)).Elif(take, first_px.eq(0)),       # armed in blanking
        ]

        # Async FIFO PCLK -> sys: (pix, sol, sof). dout is valid whenever readable.
        self.fifo = fifo = ClockDomainsRenamer({"write": "pix", "read": "sys"})(AsyncFIFO(10, 8))
        self.comb += [
            fifo.we.eq(take),
            fifo.din.eq(Cat(d, sol, sof)),
            fifo.re.eq(fifo.readable),       # always drain: one word per sys clock
        ]

        # sys: rebuild x / y / frame_id; these registers ARE the stream's x / y / frame_id ----------
        r_pix = fifo.dout[0:8]
        r_sol = fifo.dout[8]
        r_sof = fifo.dout[9]

        x        = Signal(11)
        y        = Signal(11)
        frame_id = Signal(16)
        synced   = Signal()                  # seen a sof since reset (skip a partial first frame)
        nx       = Signal(11)
        ny       = Signal(11)
        self.comb += [
            If(r_sof,
                nx.eq(0), ny.eq(0),
            ).Elif(r_sol,
                nx.eq(0), ny.eq(y + 1),
            ).Else(
                nx.eq(x + 1), ny.eq(y),
            ),
            source.x.eq(x),
            source.y.eq(y),
            source.frame_id.eq(frame_id),
            self.frame_id.status.eq(frame_id),   # same register, no extra flip-flops
        ]
        self.sync += [
            source.valid.eq(0), source.sof.eq(0), source.eol.eq(0), source.eof.eq(0),
            If(fifo.readable,
                x.eq(nx), y.eq(ny),
                If(r_sof, synced.eq(1), frame_id.eq(frame_id + 1)),
                If((synced | r_sof) & (nx < width) & (ny < height),
                    source.valid.eq(1),
                    source.pix.eq(r_pix),
                    source.sof.eq(r_sof),
                    source.eol.eq(nx == width - 1),
                    source.eof.eq((nx == width - 1) & (ny == height - 1)),
                ),
            ),
        ]
