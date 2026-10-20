/* main.c -- board check: camera front-end, Watch and Snitch, reported as text over the UART.
 * SoC: python3 -m soc.tang4k --cpu-type=gowin_emcu --with-camera --cam-ip frontend --with-vision
 *                            --uart-baudrate 3000000 --build --no-compile-gateware
 * Objects: crt0.o main.o vision.o
 *
 *   every 200 ms  "w t=..s f=FRAME fps=.. cal act rdy lost chg=N/192"
 *   on an event   "EVENT frame=.. tiles=.. bbox x..-.. y..-.."
 *                 then a Snitch self-check: one picture captured and verified on the M3
 *                 (headers, CRC, all rows once); only this summary goes out:
 *                 "snitch: picture=N rows=600/600 dup=0 bad_hdr=0 bad_crc=0 frames=a..b OK"
 *   keys          s = Snitch self-check now, r = restart Watch (re-calibrates)
 */
#include <stdio.h>
#include <stdint.h>
#include <generated/csr.h>
#include <generated/soc.h>
#include <libbase/uart.h>
#include <system.h>
#include "vision.h"

#define POLL_MS  200
#define N_TILES  (VIS_WATCH_TILES_X * VIS_WATCH_TILES_Y)

/* Snitch self-check --------------------------------------------------------------------------- */

static uint16_t crc16(const uint8_t *p, int n)      /* CRC-16/CCITT-FALSE, as in the fabric */
{
    uint16_t c = 0xFFFF;
    while (n--) {
        c ^= (uint16_t)*p++ << 8;
        for (int i = 0; i < 8; i++)
            c = (c & 0x8000) ? (c << 1) ^ 0x1021 : c << 1;
    }
    return c;
}

static snitch_packet_t pkt;
static uint8_t seen[(VIS_HEIGHT + 7) / 8];

static void snitch_check(void)
{
    unsigned rows = 0, dup = 0, bad_hdr = 0, bad_crc = 0;
    uint16_t f_first = 0, f_last = 0;
    for (unsigned i = 0; i < sizeof seen; i++)
        seen[i] = 0;

    snitch_start();
    while (snitch_busy()) {
        if (!snitch_ready())
            continue;
        snitch_read(pkt);
        const uint8_t *b = (const uint8_t *)pkt;
        unsigned y  = pkt[0] >> 16;
        uint16_t fr = pkt[1] & 0xffff;
        if (b[0] != 'R' || b[1] != 'W' || y >= VIS_HEIGHT) {
            bad_hdr++;
            continue;
        }
        if (crc16(b + 8, VIS_WIDTH) != (pkt[SNITCH_PACKET_WORDS - 1] & 0xffff))
            bad_crc++;
        if (seen[y >> 3] & (1 << (y & 7))) {
            dup++;
        } else {
            seen[y >> 3] |= 1 << (y & 7);
            if (rows++ == 0)
                f_first = fr;
            f_last = fr;
        }
    }
    int ok = rows == VIS_HEIGHT && !dup && !bad_hdr && !bad_crc;
    printf("snitch: picture=%u rows=%u/%u dup=%u bad_hdr=%u bad_crc=%u frames=%u..%u %s\n",
           snitch_picture(), rows, VIS_HEIGHT, dup, bad_hdr, bad_crc, f_first, f_last,
           ok ? "OK" : "FAIL");
}

/* Main ---------------------------------------------------------------------------------------- */

int main(void)
{
    uart_init();
    printf("\n== Tang Nano 4K vision check %ux%u | watch %ux%u tiles, every %u frames ==\n",
           VIS_WIDTH, VIS_HEIGHT, VIS_WATCH_TILES_X, VIS_WATCH_TILES_Y, VIS_WATCH_STEP);
    printf("camera: cfg_done=%lu\n",
           (unsigned long)(fe_status_read() >> CSR_FE_STATUS_CFG_DONE_OFFSET) & 1);
    watch_start();

    unsigned ms = 0, t_ds = 0;                   /* t_ds: time in tenths of a second */
    uint16_t f_prev = watch_frame();

    for (;;) {
        busy_wait(1);

        /* Watch: status every 200 ms */
        if (++ms >= POLL_MS) {
            ms = 0;
            t_ds += POLL_MS / 100;
            watch_status_t w;
            watch_status(&w);
            uint16_t f = watch_frame();
            printf("w t=%u.%us f=%u fps=%u cal=%u act=%u rdy=%u lost=%u chg=%u/%u\n",
                   t_ds / 10, t_ds % 10, f, (uint16_t)(f - f_prev) * (1000 / POLL_MS),
                   w.calibrating, w.active, w.ready, w.lost, w.changed, N_TILES);
            f_prev = f;
        }

        /* Watch event: report, then check the Snitch (no picture over the UART) */
        if (watch_ready()) {
            watch_event_t ev;
            watch_read(&ev);
            printf("EVENT frame=%u tiles=%u bbox x%u-%u y%u-%u\n",
                   ev.frame, ev.tiles, ev.x0, ev.x1, ev.y0, ev.y1);
            snitch_check();
            f_prev = watch_frame();
        }

        /* keys */
        if (uart_read_nonblock()) {
            switch (uart_read()) {
            case 's': snitch_check(); f_prev = watch_frame(); break;
            case 'r': watch_start(); printf("watch: restarted (calibrating)\n"); break;
            }
        }
    }
}
