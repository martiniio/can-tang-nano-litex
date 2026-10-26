/* vision.c -- M3 driver for the Vision IP. See vision.h. */
#include <generated/csr.h>
#include "vision.h"

/* Field of vis_snitch_status, e.g. SNITCH_ST(st, LEVEL). */
#define SNITCH_ST(v, f) (((v) >> CSR_VIS_SNITCH_STATUS_##f##_OFFSET) & \
                         ((1u << CSR_VIS_SNITCH_STATUS_##f##_SIZE) - 1))

/* Snitch ------------------------------------------------------------------------------------- */

void snitch_start(void) { vis_snitch_ctrl_write(1 << CSR_VIS_SNITCH_CTRL_START_OFFSET); }
void snitch_stop(void)  { vis_snitch_ctrl_write(1 << CSR_VIS_SNITCH_CTRL_STOP_OFFSET);  }

int      snitch_busy(void)    { return SNITCH_ST(vis_snitch_status_read(), BUSY);    }
int      snitch_ready(void)   { return SNITCH_ST(vis_snitch_status_read(), LEVEL);   }
unsigned snitch_picture(void) { return SNITCH_ST(vis_snitch_status_read(), PICTURE); }
unsigned snitch_rows(void)    { return SNITCH_ST(vis_snitch_status_read(), ROWS);    }

void snitch_read(snitch_packet_t pkt)
{
    /* Each read of vis_snitch_data returns the next word; the fabric removes the packet from
     * the queue after its last word. */
    for (int i = 0; i < SNITCH_PACKET_WORDS; i++)
        pkt[i] = vis_snitch_data_read();
}

/* Watch -------------------------------------------------------------------------------------- */

#define WATCH_ST(v, f) (((v) >> CSR_VIS_WATCH_STATUS_##f##_OFFSET) & \
                        ((1u << CSR_VIS_WATCH_STATUS_##f##_SIZE) - 1))

void watch_start(void) { vis_watch_ctrl_write(1 << CSR_VIS_WATCH_CTRL_START_OFFSET); }
void watch_stop(void)  { vis_watch_ctrl_write(1 << CSR_VIS_WATCH_CTRL_STOP_OFFSET);  }

int watch_ready(void) { return WATCH_ST(vis_watch_status_read(), READY); }

void watch_read(watch_event_t *ev)
{
    uint32_t e0 = vis_watch_event0_read();
    uint32_t e1 = vis_watch_event1_read();      /* this read takes the event */
    ev->frame = e0 & 0xffff;
    ev->tiles = (e0 >> 16) & 0xff;
    ev->x0 = e1 & 15;  ev->x1 = (e1 >> 4) & 15;
    ev->y0 = (e1 >> 8) & 15;  ev->y1 = (e1 >> 12) & 15;
}

void watch_status(watch_status_t *st)
{
    uint32_t v = vis_watch_status_read();
    st->running     = WATCH_ST(v, RUNNING);
    st->calibrating = WATCH_ST(v, CALIBRATING);
    st->active      = WATCH_ST(v, ACTIVE);
    st->ready       = WATCH_ST(v, READY);
    st->lost        = WATCH_ST(v, LOST);
    st->changed     = WATCH_ST(v, CHANGED);
}

uint16_t watch_frame(void) { return vis_watch_frame_read(); }
