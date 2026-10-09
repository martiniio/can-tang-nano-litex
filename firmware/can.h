/* can.h -- tiny driver for the CanLite LiteX block (registers from generated/csr.h). */
#ifndef CAN_H
#define CAN_H

#include <stdint.h>
#include <generated/csr.h>

/* LiteX generates FOO_OFFSET / FOO_SIZE for every CSRField; this extracts one. */
#define CSR_FIELD_GET(value, FIELD) \
    (((value) >> FIELD##_OFFSET) & ((1u << FIELD##_SIZE) - 1u))

typedef struct {
    uint32_t id;        /* 11 or 29 bits, right-aligned */
    uint8_t  ide;       /* 1 = extended frame            */
    uint8_t  rtr;
    uint8_t  dlc;       /* 0..8                          */
    uint8_t  data[8];
    uint32_t ts;        /* RX timestamp, microseconds    */
} can_frame_t;

enum { CAN_ERROR_ACTIVE = 0, CAN_ERROR_PASSIVE = 1, CAN_BUS_OFF = 2, CAN_RESET = 3 };

int      can_tx_free(void);                     /* frames that still fit in the TX FIFO */
int      can_rx_pending(void);                  /* frames waiting in the RX FIFO        */
int      can_state(void);                       /* CAN_ERROR_ACTIVE ...                 */
int      can_send(const can_frame_t *f);        /* 0 = queued, -1 = TX FIFO full        */
int      can_recv(can_frame_t *f);              /* 1 = got one, 0 = none                */
uint32_t can_time_us(void);

#endif
