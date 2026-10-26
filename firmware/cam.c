/* cam.c -- OV2640 strip capture driver. Register meaning: see cores/dvp_camera.py. */
#include <generated/csr.h>
#include <system.h>           /* busy_wait_us (timer0) */
#include "cam.h"

static uint32_t ctrl_keep;    /* y_phase is a level bit in the same register as the pulses */

int cam_config_done(void)
{
    return (cam_status_read() >> CSR_CAM_STATUS_CFG_DONE_OFFSET) & 1;
}

void cam_reconfigure(void)
{
    cam_ctrl_write(ctrl_keep | (1 << CSR_CAM_CTRL_RESEND_OFFSET));
}

void cam_set_y_phase(int phase)
{
    ctrl_keep = phase ? (1 << CSR_CAM_CTRL_Y_PHASE_OFFSET) : 0;
    cam_ctrl_write(ctrl_keep);
}

void cam_debug(cam_dbg_t *d)
{
    d->frames   = cam_dbg_frames_read();
    d->line_len = cam_dbg_line_len_read();
    d->lines    = cam_dbg_lines_read();
}

int cam_capture_strip(int row_start, uint32_t timeout_ms)
{
    cam_row_start_write(row_start);
    cam_ctrl_write(ctrl_keep | (1 << CSR_CAM_CTRL_ARM_OFFSET));   /* also clears status.done */
    busy_wait_us(10);                                            /* let the clear reach sys */
    for (uint32_t t = 0; t < timeout_ms * 10; t++) {
        if ((cam_status_read() >> CSR_CAM_STATUS_DONE_OFFSET) & 1)
            return 0;
        busy_wait_us(100);
    }
    return -1;
}

void cam_read_row(int y, uint8_t *dst)
{
    uint32_t base = (uint32_t)y * (CAM_WIDTH / 4);
    for (int i = 0; i < CAM_WIDTH / 4; i++) {
        cam_rd_addr_write(base + i);
        uint32_t w = cam_rd_data_read();                /* byte 0 = leftmost pixel */
        dst[4*i + 0] = w;
        dst[4*i + 1] = w >> 8;
        dst[4*i + 2] = w >> 16;
        dst[4*i + 3] = w >> 24;
    }
}
