/* capture.c -- M3 driver for Capture. See capture.h. */
#include <generated/csr.h>
#include "capture.h"

/* Field of cap_status, e.g. CAP_ST(st, LEVEL). */
#define CAP_ST(v, f) (((v) >> CSR_CAP_STATUS_##f##_OFFSET) & ((1u << CSR_CAP_STATUS_##f##_SIZE) - 1))

void capture_start(void) { cap_ctrl_write(1 << CSR_CAP_CTRL_START_OFFSET); }
void capture_stop(void)  { cap_ctrl_write(1 << CSR_CAP_CTRL_STOP_OFFSET);  }

int      capture_busy(void)    { return CAP_ST(cap_status_read(), BUSY);    }
int      capture_ready(void)   { return CAP_ST(cap_status_read(), LEVEL);   }
unsigned capture_picture(void) { return CAP_ST(cap_status_read(), PICTURE); }
unsigned capture_rows(void)    { return CAP_ST(cap_status_read(), ROWS);    }

void capture_read(capture_packet_t pkt)
{
    /* Each read of cap_data returns the next word; the fabric removes the packet from the queue
     * after its last word. */
    for (int i = 0; i < CAPTURE_PACKET_WORDS; i++)
        pkt[i] = cap_data_read();
}
