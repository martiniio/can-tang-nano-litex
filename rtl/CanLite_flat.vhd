--------------------------------------------------------------------------------
-- CanLite_flat.vhd
--
-- Thin shim around CanLite (unchanged upstream core) for instantiation from a
-- Verilog top (LiteX). Verilog cannot connect to VHDL records/enums, so this
-- file only flattens them into plain std_logic_vectors. No logic of its own,
-- apart from clearing unused ID bits on received standard (11-bit) frames.
--
-- Frame layout (both directions):
--   id   : 29 bits, right-aligned (standard frames use id(10 downto 0))
--   data : 64 bits, byte 0 = data(7 downto 0) ... byte 7 = data(63 downto 56)
-- state  : "00" error-active, "01" error-passive, "10" bus-off, "11" reset
--------------------------------------------------------------------------------
library ieee;
    use ieee.std_logic_1164.all;
library work;
    use work.CanBus;

entity CanLite_flat is
    generic (
        BAUD_RATE_PRESCALAR        : positive range 1 to 64 := 3;
        SYNCHRONIZATION_JUMP_WIDTH : positive range 1 to 4  := 2;
        TIME_SEGMENT_1             : positive range 1 to 16 := 6;
        TIME_SEGMENT_2             : positive range 1 to 8  := 2;
        TRIPLE_SAMPLING            : natural range 0 to 1 := 1  -- 1 = true (integer: Verilog-friendly)
    );
    port (
        clk           : in  std_logic;
        rst_n         : in  std_logic;
        can_rx        : in  std_logic;
        can_tx        : out std_logic;

        -- TX: the core pulls a frame (classic FIFO read; frame must then stay stable)
        tx_id         : in  std_logic_vector(28 downto 0);
        tx_rtr        : in  std_logic;
        tx_ide        : in  std_logic;
        tx_dlc        : in  std_logic_vector(3 downto 0);
        tx_data       : in  std_logic_vector(63 downto 0);
        tx_rd_en      : out std_logic;
        tx_empty      : in  std_logic;
        tx_ack        : out std_logic;

        -- RX: the core pushes a frame (valid in the cycle rx_wr_en = '1')
        rx_id         : out std_logic_vector(28 downto 0);
        rx_rtr        : out std_logic;
        rx_ide        : out std_logic;
        rx_dlc        : out std_logic_vector(3 downto 0);
        rx_data       : out std_logic_vector(63 downto 0);
        rx_wr_en      : out std_logic;
        rx_full       : in  std_logic;

        -- Status
        state         : out std_logic_vector(1 downto 0);
        error_warning : out std_logic;
        overflow      : out std_logic
    );
end entity CanLite_flat;

architecture shim of CanLite_flat is
    signal tx_frame : CanBus.Frame;
    signal rx_frame : CanBus.Frame;
    signal status   : CanBus.Status;
begin

    core : entity work.CanLite
        generic map (
            BAUD_RATE_PRESCALAR        => BAUD_RATE_PRESCALAR,
            SYNCHRONIZATION_JUMP_WIDTH => SYNCHRONIZATION_JUMP_WIDTH,
            TIME_SEGMENT_1             => TIME_SEGMENT_1,
            TIME_SEGMENT_2             => TIME_SEGMENT_2,
            TRIPLE_SAMPLING            => (TRIPLE_SAMPLING = 1)
        )
        port map (
            Clock             => clk,
            Reset_n           => rst_n,
            CanRx             => can_rx,
            CanTx             => can_tx,
            RxFrame           => rx_frame,
            RxFifoWriteEnable => rx_wr_en,
            RxFifoFull        => rx_full,
            TxFrame           => tx_frame,
            TxFifoReadEnable  => tx_rd_en,
            TxFifoEmpty       => tx_empty,
            TxAck             => tx_ack,
            Status            => status
        );

    -- TX: vectors -> record
    tx_frame.Id   <= tx_id;
    tx_frame.Rtr  <= tx_rtr;
    tx_frame.Ide  <= tx_ide;
    tx_frame.Dlc  <= tx_dlc;
    tx_frame.Data <= CanBus.to_DataBytes(tx_data);

    -- RX: record -> vectors (clear the unused upper ID bits of standard frames)
    rx_id   <= rx_frame.Id when rx_frame.Ide = '1'
               else (28 downto 11 => '0') & rx_frame.Id(10 downto 0);
    rx_rtr  <= rx_frame.Rtr;
    rx_ide  <= rx_frame.Ide;
    rx_dlc  <= rx_frame.Dlc;
    rx_data <= CanBus.to_std_logic_vector(rx_frame.Data);

    -- Status: record/enum -> bits
    with status.State select state <=
        "00" when CanBus.STATE_ERROR_ACTIVE,
        "01" when CanBus.STATE_ERROR_PASSIVE,
        "10" when CanBus.STATE_BUS_OFF,
        "11" when others;
    error_warning <= status.ErrorWarning;
    overflow      <= status.Overflow;

end architecture shim;
