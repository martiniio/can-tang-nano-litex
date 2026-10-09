/* can.c -- see can.h */
#include "can.h"

#define CAN_FIFO_DEPTH 4   /* fifo_depth in canlite.py */

int can_rx_pending(void) { return CSR_FIELD_GET(can_status_read(), CSR_CAN_STATUS_RX_LEVEL); }
int can_tx_free(void)    { return CAN_FIFO_DEPTH - CSR_FIELD_GET(can_status_read(), CSR_CAN_STATUS_TX_LEVEL); }
int can_state(void)      { return CSR_FIELD_GET(can_status_read(), CSR_CAN_STATUS_STATE); }
uint32_t can_time_us(void) { return can_timestamp_read(); }

int can_send(const can_frame_t *f)
{
    if (can_tx_free() <= 0)
        return -1;

    uint64_t data = 0;
    for (int i = 0; i < 8; i++)
        data |= (uint64_t)f->data[i] << (8 * i);       /* byte 0 = bits 7:0 */

    can_tx_id_write(f->id);
    can_tx_ctrl_write(((uint32_t)(f->rtr & 1) << CSR_CAN_TX_CTRL_RTR_OFFSET) |
                      ((uint32_t)(f->ide & 1) << CSR_CAN_TX_CTRL_IDE_OFFSET) |
                      ((uint32_t)(f->dlc & 15) << CSR_CAN_TX_CTRL_DLC_OFFSET));
    can_tx_data_write(data);
    can_cmd_write(1u << CSR_CAN_CMD_TX_PUSH_OFFSET);   /* pulse: push into TX FIFO */
    return 0;
}

int can_recv(can_frame_t *f)
{
    if (can_rx_pending() == 0)
        return 0;

    uint32_t ctrl = can_rx_ctrl_read();
    uint64_t data = can_rx_data_read();

    f->id  = can_rx_id_read();
    f->rtr = CSR_FIELD_GET(ctrl, CSR_CAN_RX_CTRL_RTR);
    f->ide = CSR_FIELD_GET(ctrl, CSR_CAN_RX_CTRL_IDE);
    f->dlc = CSR_FIELD_GET(ctrl, CSR_CAN_RX_CTRL_DLC);
    for (int i = 0; i < 8; i++)
        f->data[i] = (uint8_t)(data >> (8 * i));
    f->ts  = can_rx_ts_read();

    can_cmd_write(1u << CSR_CAN_CMD_RX_POP_OFFSET);    /* pulse: drop it from the RX FIFO */
    return 1;
}
