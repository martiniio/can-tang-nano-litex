/* main.c -- CAN demo / canfdtest DUT.
 *
 * CANFDTEST_DUT = 1 : silent echo for `canfdtest -g can0` on the other node:
 *                     every received frame goes back with id+1 and each data byte +1.
 *                     No printing in the loop (printf at 115200 baud is slower than CAN
 *                     and would overflow the 4-deep RX FIFO).
 * CANFDTEST_DUT = 0 : demo: print every frame, echo with id+1, heartbeat 0x100 each second.
 */
#include <stdio.h>
#include <generated/csr.h>
#include <generated/soc.h>
#include <libbase/uart.h>
#include "can.h"

#define CANFDTEST_DUT 1

static const char *state_name[] = { "error-active", "error-passive", "bus-off", "reset" };

int main(void)
{
#ifdef CSR_UART_BASE
    uart_init();
#endif
    printf("\n== Tang Nano 4K CAN %s (%lu Hz) ==\n",
           CANFDTEST_DUT ? "canfdtest DUT" : "demo", (unsigned long)CONFIG_CLOCK_FREQUENCY);
    printf("state: %s\n", state_name[can_state()]);

    can_frame_t hb = { .id = 0x100, .dlc = 4 };
    uint32_t next_hb = can_time_us();
    uint32_t count = 0;
    uint32_t echoed = 0;
    int last_state = can_state();

    for (;;) {
        can_frame_t f;
        while (can_recv(&f)) {
#if !CANFDTEST_DUT
            printf("[%10lu us] RX id=0x%03lx dlc=%u data=",
                   (unsigned long)f.ts, (unsigned long)f.id, f.dlc);
            for (int i = 0; i < f.dlc && i < 8; i++) printf("%02x ", f.data[i]);
            printf("\n");
#endif
            f.id = (f.id + 1) & (f.ide ? 0x1FFFFFFF : 0x7FF);       /* id + 1    */
#if CANFDTEST_DUT
            for (int i = 0; i < f.dlc && i < 8; i++) f.data[i]++;  /* bytes + 1 */
#endif
            while (can_send(&f) != 0) ;                              /* wait for TX FIFO space */
            echoed++;
        }

        if ((int32_t)(can_time_us() - next_hb) >= 0) {               /* once per 10 second */
            next_hb += 10000000;
#if CANFDTEST_DUT
            if (echoed) printf("echoed %lu frames\n", (unsigned long)echoed);
#else
            hb.data[0] = count; hb.data[1] = count >> 8; hb.data[2] = count >> 16; hb.data[3] = count >> 24;
            count++;
            if (can_send(&hb) != 0) printf("TX FIFO full\n");
#endif
        }

        int s = can_state();
        if (s != last_state) {
            printf("state -> %s\n", state_name[s]);
            last_state = s;
        }
    }
}
