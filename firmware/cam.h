/* cam.h -- OV2640 strip capture (cores/dvp_camera.py). Needs a --with-camera SoC build. */
#ifndef CAM_H
#define CAM_H

#include <stdint.h>
#include <generated/soc.h>   /* CAM_WIDTH, CAM_HEIGHT, CAM_ROWS, CAM_YUV */

typedef struct {
    uint16_t frames;    /* VSYNC rising edges since power-up (should keep counting) */
    uint16_t line_len;  /* PCLKs per HREF line (width in raw, 2*width in yuv)       */
    uint16_t lines;     /* HREF lines per frame (should be >= CAM_HEIGHT)           */
} cam_dbg_t;

int  cam_config_done(void);                       /* SCCB init table finished          */
void cam_reconfigure(void);                       /* re-run the SCCB init table        */
void cam_set_y_phase(int phase);                  /* YUV: which byte of a pair is Y    */
void cam_debug(cam_dbg_t *d);
int  cam_capture_strip(int row_start, uint32_t timeout_ms);  /* 0 ok, -1 timeout     */
void cam_read_row(int y, uint8_t *dst);           /* row y (0..CAM_ROWS-1) of the strip */

#endif
