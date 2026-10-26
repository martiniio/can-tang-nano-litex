/* main.c -- camera node: on request, one full 800x600 greyscale picture over the UART.
 * SoC: python3 -m soc.tang4k --cpu-type=gowin_emcu --with-camera
 *                            --uart-baudrate 3000000 --build --no-compile-gateware
 * Objects: crt0.o main.o capture.o
 * Receiver: tools/node_mon.py (Luckfox)
 *
 * UART output (text lines, except the binary block of a picture):
 *   every 1 s     "cam t=..s f=FRAME fps=.. cfg=.."
 *                 t    time since boot          f    frame_id of the camera stream
 *                 fps  frames in the last second
 *                 cfg  sensor configured (SCCB table sent)
 *   a picture     "PIC picture=N rows=600 bytes=812"
 *                 600 binary row packets of 812 bytes (layout: capture.h), PIC_GAP_US apart
 *                 "PIC_END picture=N frames=a..b"
 *   keys          s = send a picture
 *                 t = drain test: take a picture but only read it (no UART), print
 *                     "drain: picture=N rows=.. frames=a..b": how fast fabric + M3 alone are
 *
 * The CRC is made by the fabric and checked by the receiver (end to end); the M3 only forwards.
 */
#include <stdio.h>
#include <stdint.h>
#include <generated/csr.h>
#include <generated/soc.h>
#include <libbase/uart.h>
#include <system.h>
#include "capture.h"

#define STATUS_MS   1000
#define PIC_GAP_US  500        /* line idle after each packet: the Luckfox UART driver needs it */

static capture_packet_t pkt;

static void tx(uint8_t c)
{
    while (uart_txfull_read());
    uart_rxtx_write(c);
}

/* Take one picture; send = 1: forward every packet over the UART, 0: only read it (drain test).
 * Returns the number of packets; *f_first / *f_last: camera frames of the first / last packet. */
static unsigned picture(int send, uint16_t *f_first, uint16_t *f_last)
{
    unsigned n = 0;
    capture_start();
    while (capture_busy()) {
        if (!capture_ready())
            continue;
        capture_read(pkt);
        if (n++ == 0)
            *f_first = pkt[1] & 0xffff;
        *f_last = pkt[1] & 0xffff;
        if (send) {
            const uint8_t *b = (const uint8_t *)pkt;
            for (int i = 0; i < CAPTURE_PACKET_BYTES; i++)
                tx(b[i]);
            busy_wait_us(PIC_GAP_US);
        }
    }
    return n;
}

static void send_picture(void)
{
    uint16_t a = 0, b = 0;
    printf("PIC picture=%u rows=%u bytes=%u\n",
           (capture_picture() + 1) & 0xff, CAP_HEIGHT, CAPTURE_PACKET_BYTES);
    picture(1, &a, &b);
    printf("PIC_END picture=%u frames=%u..%u\n", capture_picture(), a, b);
}

static void drain_test(void)
{
    uint16_t a = 0, b = 0;
    unsigned n = picture(0, &a, &b);
    printf("drain: picture=%u rows=%u frames=%u..%u (%u frames)\n",
           capture_picture(), n, a, b, (uint16_t)(b - a) + 1);
}

int main(void)
{
    uart_init();
    printf("\n== Tang Nano 4K camera node %ux%u | s = picture, t = drain test ==\n",
           CAP_WIDTH, CAP_HEIGHT);

    unsigned ms = 0, t_s = 0;
    uint16_t f_prev = fe_frame_id_read();

    for (;;) {
        busy_wait(1);

        /* status every second */
        if (++ms >= STATUS_MS) {
            ms = 0;
            t_s++;
            uint16_t f = fe_frame_id_read();
            printf("cam t=%us f=%u fps=%u cfg=%lu\n", t_s, f, (uint16_t)(f - f_prev),
                   (unsigned long)(fe_status_read() >> CSR_FE_STATUS_CFG_DONE_OFFSET) & 1);
            f_prev = f;
        }

        /* keys */
        if (uart_read_nonblock()) {
            switch (uart_read()) {
            case 's': send_picture(); break;
            case 't': drain_test();   break;
            default:  continue;
            }
            ms = 0;                              /* a picture paused the loop: restart the window */
            f_prev = fe_frame_id_read();
        }
    }
}
