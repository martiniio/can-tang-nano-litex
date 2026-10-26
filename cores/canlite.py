"""
canlite.py -- LiteX wrapper around the CanLite classical-CAN core (VHDL).

The VHDL core is unchanged. rtl/CanLite_flat.vhd flattens its record ports so the
Verilog side can instantiate it.

Data path
    CPU -> tx_* registers -> TX FIFO -> tx hold register -> core -> CAN bus
    CAN bus -> core -> acceptance filter -> RX FIFO -> rx_* registers -> CPU

Interrupts: one IRQ line, two sources. Firmware reads ev.pending to see which fired.
    rx     level  RX FIFO holds at least one frame
    error  pulse  bus fault started (warning, overflow or bus-off), or a frame was
                  dropped and the dropped flag was not yet cleared

Polled, no interrupt
    rx_dropped, tx_dropped   saturating counts of frames lost to a full FIFO
    status.dropped           sticky flag, cleared by command.clear_drops
    tx_level                 TX FIFO fill; poll it to know when a transmit has left

Bit timing (fixed at build time): BRP 3, TSEG1 6, TSEG2 2 at 27 MHz
    -> 500 kbit/s, sample point 78 %
"""

import os

from migen import *

from litex.gen import LiteXModule
from litex.soc.interconnect import stream
from litex.soc.interconnect.csr import CSRStatus, CSRStorage, CSRField
from litex.soc.interconnect.csr_eventmanager import EventManager, EventSourceLevel, EventSourcePulse

# One CAN frame as the FIFOs and the VHDL core see it.
# Same field order and widths as the CanLite_flat ports.
FRAME_LAYOUT = [
    ("id",   29),   # right-aligned; standard frames use id[10:0]
    ("rtr",   1),
    ("ide",   1),   # 1 = extended (29-bit) identifier
    ("dlc",   4),
    ("data", 64),   # byte 0 = data[7:0] ... byte 7 = data[63:56]
]
RX_FRAME_LAYOUT = FRAME_LAYOUT + [("ts", 32)]   # received frames carry a 1 us timestamp

BUS_OFF     = 0b10
COUNTER_MAX = 2**16 - 1


def count_saturating(counter, condition):
    """Increment counter.status on each cycle the condition holds, stopping at COUNTER_MAX."""
    return If(condition & (counter.status != COUNTER_MAX),
        counter.status.eq(counter.status + 1),
    )


class CanLite(LiteXModule):
    def __init__(self, platform, pads, sys_clk_freq, fifo_depth=4,
                 brp=3, sjw=2, tseg1=6, tseg2=2, triple_sampling=True):

        # ============================================================ CSR registers
        self._add_registers()

        # ============================================================ interrupts
        self.ev = EventManager()
        self.ev.rx    = EventSourceLevel(description="RX FIFO holds at least one frame.")
        self.ev.error = EventSourcePulse(description="Bus fault started, or a frame was dropped.")
        self.ev.finalize()

        # ============================================================ FIFOs
        # First-word-fall-through. sink = write side, source = read side.
        # A word moves when valid and ready are both high in the same cycle.
        self.tx_fifo = tx_fifo = stream.SyncFIFO(FRAME_LAYOUT, fifo_depth)
        self.rx_fifo = rx_fifo = stream.SyncFIFO(RX_FRAME_LAYOUT, fifo_depth)

        # ============================================================ 1 us timestamp
        clocks_per_us = int(sys_clk_freq // 1e6)          # 27 at 27 MHz
        us_divider    = Signal(max=clocks_per_us)
        self.sync += If(us_divider == clocks_per_us - 1,
            us_divider.eq(0),
            self.timestamp.status.eq(self.timestamp.status + 1),
        ).Else(
            us_divider.eq(us_divider + 1),
        )

        # ============================================================ signals shared with the core
        tx_take       = Signal()                  # core takes the head frame from the TX FIFO
        tx_done       = Signal()                  # core finished transmitting a frame
        tx_frame      = Record(FRAME_LAYOUT)      # copy of the frame on the bus, held stable
        rx_valid      = Signal()                  # core has a received frame on rx_frame
        rx_frame      = Record(FRAME_LAYOUT)
        bus_state     = Signal(2)
        error_warning = Signal()
        core_overflow = Signal()

        # ============================================================ CPU -> TX FIFO
        # A push into a full FIFO is lost, and that loss is counted.
        push    = self.command.fields.tx_push
        tx_lost = Signal()
        self.comb += [
            tx_fifo.sink.valid.eq(push & tx_fifo.sink.ready),
            tx_lost.eq(push & ~tx_fifo.sink.ready),
            tx_fifo.sink.id.eq(self.tx_id.storage),
            tx_fifo.sink.rtr.eq(self.tx_ctrl.fields.rtr),
            tx_fifo.sink.ide.eq(self.tx_ctrl.fields.ide),
            tx_fifo.sink.dlc.eq(self.tx_ctrl.fields.dlc),
            tx_fifo.sink.data.eq(self.tx_data.storage),
            tx_fifo.source.ready.eq(tx_take),
        ]
        self.sync += [
            # The core reads the frame for the whole transmission, so latch it when taken.
            If(tx_take & tx_fifo.source.valid,
                *[getattr(tx_frame, n).eq(getattr(tx_fifo.source, n)) for n, _ in FRAME_LAYOUT],
            ),
            count_saturating(self.tx_dropped, tx_lost),
        ]

        # ============================================================ core -> filter -> RX FIFO
        accept  = Signal()
        rx_lost = Signal()
        self.comb += [
            # With the filter off, every frame is accepted.
            accept.eq(~self.filter_ctrl.fields.enable |
                      (((rx_frame.id ^ self.filter_id.storage) & self.filter_mask.storage) == 0)),
            rx_fifo.sink.valid.eq(rx_valid & accept),
            *[getattr(rx_fifo.sink, n).eq(getattr(rx_frame, n)) for n, _ in FRAME_LAYOUT],
            rx_fifo.sink.ts.eq(self.timestamp.status),
            # An accepted frame that finds the RX FIFO full is lost.
            rx_lost.eq(rx_valid & accept & ~rx_fifo.sink.ready),
        ]
        self.sync += count_saturating(self.rx_dropped, rx_lost)

        # ============================================================ RX FIFO -> CPU
        self.comb += [
            self.rx_id.status.eq(rx_fifo.source.id),
            self.rx_ctrl.fields.rtr.eq(rx_fifo.source.rtr),
            self.rx_ctrl.fields.ide.eq(rx_fifo.source.ide),
            self.rx_ctrl.fields.dlc.eq(rx_fifo.source.dlc),
            self.rx_data.status.eq(rx_fifo.source.data),
            self.rx_ts.status.eq(rx_fifo.source.ts),
            rx_fifo.source.ready.eq(self.command.fields.rx_pop),
        ]

        # ============================================================ status, dropped flag, events
        dropped   = Signal()     # sticky: a frame or push was lost since the last clear
        dropped_d = Signal()
        fault     = Signal()     # warning, overflow, or bus-off is active
        fault_d   = Signal()

        self.sync += [
            If(self.command.fields.clear_drops,
                dropped.eq(0),
            ).Elif(tx_lost | rx_lost,
                dropped.eq(1),
            ),
            dropped_d.eq(dropped),
            fault_d.eq(fault),
        ]

        self.comb += [
            fault.eq(error_warning | core_overflow | (bus_state == BUS_OFF)),
            self.status.fields.rx_level.eq(rx_fifo.level),
            self.status.fields.tx_level.eq(tx_fifo.level),
            self.status.fields.bus_state.eq(bus_state),
            self.status.fields.error_warning.eq(error_warning),
            self.status.fields.dropped.eq(dropped),
            self.ev.rx.trigger.eq(rx_fifo.source.valid),
            # Pulse on the rising edge of a fault, or on the first drop since the last clear.
            self.ev.error.trigger.eq((fault & ~fault_d) | (dropped & ~dropped_d)),
        ]

        # ============================================================ the VHDL core
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

            i_tx_id    = tx_frame.id,
            i_tx_rtr   = tx_frame.rtr,
            i_tx_ide   = tx_frame.ide,
            i_tx_dlc   = tx_frame.dlc,
            i_tx_data  = tx_frame.data,
            o_tx_rd_en = tx_take,
            i_tx_empty = ~tx_fifo.source.valid,
            o_tx_ack   = tx_done,

            o_rx_id    = rx_frame.id,
            o_rx_rtr   = rx_frame.rtr,
            o_rx_ide   = rx_frame.ide,
            o_rx_dlc   = rx_frame.dlc,
            o_rx_data  = rx_frame.data,
            o_rx_wr_en = rx_valid,
            i_rx_full  = ~rx_fifo.sink.ready,

            o_state         = bus_state,
            o_error_warning = error_warning,
            o_overflow      = core_overflow,
        )

        # ============================================================ sources
        rtl = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rtl")
        platform.add_source(os.path.join(rtl, "third_party/can-lite-vhdl/src/CanBus_pkg.vhd"))
        platform.add_source(os.path.join(rtl, "third_party/can-lite-vhdl/src/CanLite.vhd"))
        platform.add_source(os.path.join(rtl, "CanLite_flat.vhd"))

    def _add_registers(self):
        # CPU -> hardware
        self.tx_id   = CSRStorage(29, description="Identifier of the next frame to send.")
        self.tx_ctrl = CSRStorage(fields=[CSRField("rtr", 1), CSRField("ide", 1), CSRField("dlc", 4)])
        self.tx_data = CSRStorage(64, description="Data bytes; byte 0 is bits 7:0.")
        self.command = CSRStorage(fields=[
            CSRField("tx_push",     1, pulse=True, description="Queue the frame in tx_*."),
            CSRField("rx_pop",      1, pulse=True, description="Consume the frame in rx_*."),
            CSRField("clear_drops", 1, pulse=True, description="Clear status.dropped."),
        ])
        self.filter_ctrl = CSRStorage(fields=[
            CSRField("enable", 1, description="1 = accept only frames that match the filter."),
        ])
        self.filter_id   = CSRStorage(29, description="Filter identifier.")
        self.filter_mask = CSRStorage(29, description="Filter mask: 1 bits must match, 0 bits are don't-care.")

        # hardware -> CPU
        self.rx_id   = CSRStatus(29)
        self.rx_ctrl = CSRStatus(fields=[CSRField("rtr", 1), CSRField("ide", 1), CSRField("dlc", 4)])
        self.rx_data = CSRStatus(64)
        self.rx_ts   = CSRStatus(32)
        self.status  = CSRStatus(fields=[
            CSRField("rx_level",      8, description="Frames waiting in the RX FIFO."),
            CSRField("tx_level",      8, description="Frames waiting in the TX FIFO."),
            CSRField("bus_state",     2, description="0 error-active, 1 error-passive, 2 bus-off."),
            CSRField("error_warning", 1, description="Error counters passed the warning limit."),
            CSRField("dropped",       1, description="A frame or push was lost since clear_drops."),
        ])
        self.timestamp  = CSRStatus(32, description="Free-running 1 us counter.")
        self.rx_dropped = CSRStatus(16, description="Received frames lost to a full RX FIFO (saturating).")
        self.tx_dropped = CSRStatus(16, description="Transmit pushes lost to a full TX FIFO (saturating).")
