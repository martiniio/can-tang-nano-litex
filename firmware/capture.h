/* capture.h -- M3 driver for Capture (cansoc/capture.py): read a full picture at your own pace.
 *
 * The fabric does the work; this is only the vocabulary to talk to it. Nothing here knows about
 * the UART or any other link: packets are read into memory and the caller decides where they go.
 *
 *     capture_start();
 *     while (capture_busy())
 *         if (capture_ready()) {
 *             capture_read(pkt);
 *             send(pkt, CAPTURE_PACKET_BYTES);
 *         }
 *
 * One packet = one row, self-describing (32-bit words, little-endian):
 *     word 0          'R' 'W' y (16 bit)
 *     word 1          frame (16 bit) | picture (8 bit) | 0
 *     words 2..W/4+1  pixels, 4 per word, byte 0 = leftmost
 *     last word       CRC-16/CCITT-FALSE of the W pixel bytes (16 bit) | 0
 * Rows the M3 is too slow for are taken from later frames, so rows arrive in any order.
 *
 * Sizes come from the build (generated/soc.h): CAP_WIDTH, CAP_HEIGHT, CAP_SLOTS, CAP_PACKET_WORDS.
 */
#ifndef CAPTURE_H
#define CAPTURE_H

#include <stdint.h>
#include <generated/soc.h>

#define CAPTURE_PACKET_WORDS CAP_PACKET_WORDS
#define CAPTURE_PACKET_BYTES (4 * CAP_PACKET_WORDS)

typedef uint32_t capture_packet_t[CAPTURE_PACKET_WORDS];

void     capture_start(void);              /* begin a new picture (queue emptied)            */
void     capture_stop(void);               /* end now (queue emptied)                         */
int      capture_busy(void);               /* picture not fully delivered yet                */
int      capture_ready(void);              /* packets waiting in the queue (0..CAP_SLOTS)    */
void     capture_read(capture_packet_t pkt); /* take the oldest packet out of the queue      */
unsigned capture_picture(void);            /* id of the current / last picture               */
unsigned capture_rows(void);               /* rows captured for this picture (of CAP_HEIGHT) */

#endif
