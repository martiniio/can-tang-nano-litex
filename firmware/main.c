
#include <stdio.h>
#include <stdint.h>
#include <generated/csr.h>
#include <generated/soc.h>
#include <libbase/uart.h>
#include <system.h>

#if defined(APP_CAN)

/* ============================================================================== APP_CAN === */
#include "can.h"

#define REPORT_US 1000000u

static uint32_t n_rx, n_tx, n_bad, n_rtr, n_self;

/* The last few echoes we queued: a received frame equal to one of them is our own. */
#define SENT_N 4
static can_frame_t sent[SENT_N];
static unsigned sent_pos;

static int same(const can_frame_t *a, const can_frame_t *b)
{
    if (a->id != b->id || a->ide != b->ide || a->dlc != b->dlc)
        return 0;
    for (int i = 0; i < a->dlc && i < 8; i++)
        if (a->data[i] != b->data[i])
            return 0;
    return 1;
}

static int is_own(const can_frame_t *f)
{
    for (int i = 0; i < SENT_N; i++)
        if (sent[i].dlc != 0xff && same(f, &sent[i]))
            return 1;
    return 0;
}

static int pattern_ok(const can_frame_t *f)       /* canfdtest: data[i] = data[0] + i */
{
    for (int i = 1; i < f->dlc && i < 8; i++)
        if ((uint8_t)(f->data[i] - f->data[i - 1]) != 1)
            return 0;
    return 1;
}

static void echo(can_frame_t *f)
{
    f->id = (f->id + 1) & (f->ide ? 0x1FFFFFFFu : 0x7FFu);
    for (int i = 0; i < f->dlc && i < 8; i++)
        f->data[i]++;
    while (can_send(f) != 0)                       /* TX FIFO full: wait for the bus */
        if (can_state() == CAN_BUS_OFF)
            return;                                /* gives up; shows up as missing echo */
    sent[sent_pos] = *f;
    sent_pos = (sent_pos + 1) % SENT_N;
    n_tx++;
}

int main(void)
{
    uart_init();
    printf("\n== Tang Nano 4K CAN echo (canfdtest DUT), 500 kbit/s classic CAN | z = zero counters ==\n");

    can_frame_t f;
    uint32_t t_last = can_time_us(), t_s = 0;
    for (int i = 0; i < SENT_N; i++)
        sent[i].dlc = 0xff;                        /* empty */

    for (;;) {
        if (can_recv(&f)) {
            n_rx++;
            if (f.rtr) {
                n_rtr++;
            } else if (is_own(&f)) {
                n_self++;
            } else {
                if (!pattern_ok(&f))
                    n_bad++;
                echo(&f);
            }
        }

        if (can_time_us() - t_last >= REPORT_US) {
            t_last += REPORT_US;
            t_s++;
            printf("can t=%lus rx=%lu tx=%lu bad=%lu rtr=%lu self=%lu lost_rx=%lu lost_tx=%lu state=%d\n",
                   (unsigned long)t_s, (unsigned long)n_rx, (unsigned long)n_tx,
                   (unsigned long)n_bad, (unsigned long)n_rtr, (unsigned long)n_self,
                   (unsigned long)can_rx_dropped_read(), (unsigned long)can_tx_dropped_read(),
                   can_state());
        }

        if (uart_read_nonblock() && uart_read() == 'z') {
            n_rx = n_tx = n_bad = n_rtr = n_self = 0;
            printf("can: counters zeroed (hardware lost_* counters keep counting)\n");
        }
    }
}

#else

/* =========================================================================== APP_CAMERA === */
#include "capture.h"

#define STATUS_MS   1000
#define PIC_GAP_US  250        /* line idle after each packet: the Luckfox UART driver needs it */

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

#endif
