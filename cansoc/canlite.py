"""
canlite.py -- LiteX wrapper around the CanLite Classical-CAN core.

Replaces CanLite_Bridge.vhd + ahb_slave_regbank.vhd. The VHDL core is unchanged;
rtl/CanLite_flat.vhd only flattens its records so this Verilog-side code can
connect to it.

    CPU --CSR--> tx_* --> TX SyncFIFO --> hold reg --> +----------------+
                                                       | CanLite_flat   | <--> can pins
    CPU <--CSR-- rx_* <-- RX SyncFIFO <--(+timestamp)--+  (VHDL)        |
    CPU <--IRQ-- EventManager (rx, tx, error)          +----------------+

Bit timing (fixed at build time, CanLite uses tq = 2 * BRP clock cycles):
    bit = 2 * BRP * (1 + TSEG1 + TSEG2) clock cycles
    27 MHz / (2 * 3 * (1 + 6 + 2)) = 27e6 / 54 = 500 kbit/s, sample point (1+6)/9 = 78 %
This is the same 9-tq bit and the same sample point your old design used at 54 MHz/BRP 6.

Register use from the CPU (addresses: see csr.csv / csr.h):
    send:    write tx_id, tx_ctrl (rtr, ide, dlc), tx_data, then cmd.tx_push = 1
    receive: while status.rx_level > 0: read rx_id, rx_ctrl, rx_data, rx_ts, then cmd.rx_pop = 1
"""

import os

from migen import *

from litex.gen import LiteXModule
from litex.soc.interconnect import stream
from litex.soc.interconnect.csr import CSRStorage, CSRStatus, CSRField
from litex.soc.interconnect.csr_eventmanager import EventManager, EventSourceLevel, EventSourcePulse

# Frame layout: the single description of a CAN frame used by the FIFOs and the core ports.
# (name, width in bits) -- same order and widths as CanLite_flat's tx_*/rx_* ports.
frame_layout = [
    ("id",   29),   # right-aligned; standard frames use id[10:0]
    ("rtr",   1),
    ("ide",   1),   # 1 = extended (29-bit) frame
    ("dlc",   4),
    ("data", 64),   # byte 0 = data[7:0] ... byte 7 = data[63:56]
]
rx_layout = frame_layout + [("ts", 32)]   # received frames also carry a 1 us timestamp


class CanLite(LiteXModule):
    def __init__(self, platform, pads, sys_clk_freq, fifo_depth=4,
                 brp=3, sjw=2, tseg1=6, tseg2=2, triple_sampling=True):

        # ------------------------------------------------------------------ registers
        # CSRStatus  = read-only for the CPU, driven by hardware (.status)
        # CSRStorage = written by the CPU, read by hardware      (.storage)
        # fields=[CSRField(...)] splits one register into named bit fields (.fields.<name>)

        # RX registers: hardware -> CPU
        self.rx_id   = CSRStatus(29)
        self.rx_ctrl = CSRStatus(fields=[CSRField("rtr", 1), CSRField("ide", 1), CSRField("dlc", 4)])
        self.rx_data = CSRStatus(64)
        self.rx_ts   = CSRStatus(32)

        # TX registers: the mirror image, but written by the CPU
        self.tx_id   = CSRStorage(29)
        self.tx_ctrl = CSRStorage(fields=[CSRField("rtr", 1), CSRField("ide", 1), CSRField("dlc", 4)])
        self.tx_data = CSRStorage(64)

        # Commands: pulse=True makes a field read as 1 for exactly one clock when the CPU writes 1.
        self.cmd = CSRStorage(fields=[
            CSRField("tx_push", 1, pulse=True, description="Queue the frame in tx_*."),
            CSRField("rx_pop",  1, pulse=True, description="Drop the frame shown in rx_*."),
        ])
        self.status = CSRStatus(fields=[
            CSRField("rx_level",      8, description="Frames waiting in the RX FIFO."),
            CSRField("tx_level",      8, description="Frames waiting in the TX FIFO."),
            CSRField("state",         2, description="0 error-active, 1 error-passive, 2 bus-off, 3 reset."),
            CSRField("error_warning", 1),
            CSRField("overflow",      1),
        ])
        self.timestamp = CSRStatus(32, description="Free-running 1 us counter.")

        # ------------------------------------------------------------------ interrupts
        self.ev = EventManager()
        self.ev.rx    = EventSourceLevel(description="RX FIFO not empty.")
        self.ev.tx    = EventSourcePulse(description="A frame was transmitted (TxAck).")
        self.ev.error = EventSourcePulse(description="Error warning, overflow or bus-off began.")
        self.ev.finalize()

        # ------------------------------------------------------------------ FIFOs
        # stream.SyncFIFO: first-word-fall-through. sink = write side, source = read side.
        # A word moves when valid & ready are both 1 in the same clock.
        self.tx_fifo = tx_fifo = stream.SyncFIFO(frame_layout, fifo_depth)
        self.rx_fifo = rx_fifo = stream.SyncFIFO(rx_layout,    fifo_depth)

        # ------------------------------------------------------------------ 1 us timestamp
        ts      = self.timestamp.status
        cycles  = int(sys_clk_freq // 1e6)        # 27 clock cycles per microsecond
        div     = Signal(max=cycles)

        # div counts 0 .. cycles-1; on wrap, ts advances by one microsecond
        self.sync += If(div == cycles - 1,
            div.eq(0),
            ts.eq(ts + 1),
        ).Else(
            div.eq(div + 1),
        )

        # ------------------------------------------------------------------ CPU -> TX FIFO
        # A write of 1 to cmd.tx_push pushes the frame currently in tx_* into the FIFO.
        self.comb += [
            tx_fifo.sink.valid.eq(self.cmd.fields.tx_push),
            tx_fifo.sink.id.eq(self.tx_id.storage),
            tx_fifo.sink.rtr.eq(self.tx_ctrl.fields.rtr),
            tx_fifo.sink.ide.eq(self.tx_ctrl.fields.ide),
            tx_fifo.sink.dlc.eq(self.tx_ctrl.fields.dlc),
            tx_fifo.sink.data.eq(self.tx_data.storage),
        ]

        # ------------------------------------------------------------------ TX FIFO -> core
        # CanLite pulses tx_rd_en to take a frame, then keeps reading TxFrame during the whole
        # transmission (and again for a retry). So we pop the FIFO on that pulse and copy the
        # frame into a hold register that stays stable until the next pulse.
        tx_rd_en = Signal()
        tx_ack   = Signal()
        hold     = Record(frame_layout)          # hold.id, hold.rtr, ... like the FIFO fields
        self.comb += tx_fifo.source.ready.eq(tx_rd_en)

        # Latch the head frame when the core takes it; it stays stable until the next pickup.
        self.sync += If(tx_rd_en & tx_fifo.source.valid,
            *[getattr(hold, n).eq(getattr(tx_fifo.source, n)) for n, _ in frame_layout],
        )

        # ------------------------------------------------------------------ core -> RX FIFO
        rx_wr_en = Signal()
        rx       = Record(frame_layout)          # frame from the core, valid while rx_wr_en = 1
        self.comb += [
            rx_fifo.sink.valid.eq(rx_wr_en),
            *[getattr(rx_fifo.sink, n).eq(getattr(rx, n)) for n, _ in frame_layout],
            rx_fifo.sink.ts.eq(ts),
        ]

        # ------------------------------------------------------------------ RX FIFO -> CPU
        self.comb += [
            self.rx_id.status.eq(rx_fifo.source.id),
            self.rx_ctrl.fields.rtr.eq(rx_fifo.source.rtr),
            self.rx_ctrl.fields.ide.eq(rx_fifo.source.ide),
            self.rx_ctrl.fields.dlc.eq(rx_fifo.source.dlc),
            self.rx_data.status.eq(rx_fifo.source.data),
            self.rx_ts.status.eq(rx_fifo.source.ts),
            rx_fifo.source.ready.eq(self.cmd.fields.rx_pop),
        ]

        # ------------------------------------------------------------------ status + events
        state = Signal(2)
        ew    = Signal()
        ov    = Signal()
        err   = Signal()
        err_d = Signal()
        self.comb += [
            self.status.fields.rx_level.eq(rx_fifo.level),
            self.status.fields.tx_level.eq(tx_fifo.level),
            self.status.fields.state.eq(state),
            self.status.fields.error_warning.eq(ew),
            self.status.fields.overflow.eq(ov),
            self.ev.rx.trigger.eq(rx_fifo.source.valid),
            self.ev.tx.trigger.eq(tx_ack),
            err.eq(ew | ov | (state == 0b10)),
            self.ev.error.trigger.eq(err & ~err_d),   # rising edge only
        ]
        self.sync += err_d.eq(err)

        # ------------------------------------------------------------------ the VHDL core
        # Instance("<entity>", p_<GENERIC>=.., i_<input>=.., o_<output>=..)
        self.specials += Instance("CanLite_flat",
            p_BAUD_RATE_PRESCALAR        = brp,
            p_SYNCHRONIZATION_JUMP_WIDTH = sjw,
            p_TIME_SEGMENT_1             = tseg1,
            p_TIME_SEGMENT_2             = tseg2,
            p_TRIPLE_SAMPLING            = int(triple_sampling),

            i_clk    = ClockSignal(),
            i_rst_n  = ~ResetSignal(),
            i_can_rx = pads.rx,
            o_can_tx = pads.tx,

            i_tx_id    = hold.id,
            i_tx_rtr   = hold.rtr,
            i_tx_ide   = hold.ide,
            i_tx_dlc   = hold.dlc,
            i_tx_data  = hold.data,
            o_tx_rd_en = tx_rd_en,
            i_tx_empty = ~tx_fifo.source.valid,
            o_tx_ack   = tx_ack,

            o_rx_id    = rx.id,
            o_rx_rtr   = rx.rtr,
            o_rx_ide   = rx.ide,
            o_rx_dlc   = rx.dlc,
            o_rx_data  = rx.data,
            o_rx_wr_en = rx_wr_en,
            i_rx_full  = ~rx_fifo.sink.ready,

            o_state         = state,
            o_error_warning = ew,
            o_overflow      = ov,
        )

        rtl = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rtl")
        platform.add_source(os.path.join(rtl, "third_party/can-lite-vhdl/src/CanBus_pkg.vhd"))                 # your GHDL-clean package
        platform.add_source(os.path.join(rtl, "third_party/can-lite-vhdl/src/CanLite.vhd"))  # upstream core, untouched
        platform.add_source(os.path.join(rtl, "CanLite_flat.vhd"))               # flattening shim
