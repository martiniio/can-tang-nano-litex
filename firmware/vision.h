/* vision.h -- M3 driver for the Vision IP (cansoc/vision.py): Snitch now, Watch later.
 *
 * The fabric does the work; this is only the vocabulary to talk to it. Nothing here knows
 * about the UART or any other link: packets are read into memory and the caller decides
 * where they go.
 *
 * Sizes come from the build (generated/soc.h): VIS_WIDTH, VIS_HEIGHT, VIS_SNITCH_SLOTS,
 * VIS_SNITCH_PACKET_WORDS.
 */
#ifndef VISION_H
#define VISION_H

#include <stdint.h>
#include <generated/soc.h>

/* Snitch ---------------------------------------------------------------------------------------
 * A queue of row packets that fills itself while a picture is being taken:
 *
 *     snitch_start();
 *     while (snitch_busy())
 *         if (snitch_ready()) {
 *             snitch_read(pkt);
 *             send(pkt, SNITCH_PACKET_BYTES);
 *         }
 *
 * One packet = one row, self-describing (32-bit words, little-endian):
 *     word 0          'R' 'W' y (16 bit)
 *     word 1          frame (16 bit) | picture (8 bit) | 0
 *     words 2..W/4+1  pixels, 4 per word, byte 0 = leftmost
 *     last word       CRC-16/CCITT-FALSE of the W pixel bytes (16 bit) | 0
 */
#define SNITCH_PACKET_WORDS VIS_SNITCH_PACKET_WORDS
#define SNITCH_PACKET_BYTES (4 * VIS_SNITCH_PACKET_WORDS)

typedef uint32_t snitch_packet_t[SNITCH_PACKET_WORDS];

void     snitch_start(void);           /* begin a new picture (queue emptied)          */
void     snitch_stop(void);            /* end now (queue emptied)                       */
int      snitch_busy(void);            /* picture not fully delivered yet              */
int      snitch_ready(void);           /* packets waiting in the queue (0..SLOTS)      */
void     snitch_read(snitch_packet_t pkt); /* take the oldest packet out of the queue  */
unsigned snitch_picture(void);         /* id of the current / last picture             */
unsigned snitch_rows(void);            /* rows captured for this picture (of VIS_HEIGHT) */

/* Watch ----------------------------------------------------------------------------------------
 * Motion detection in the fabric (Frigate's logic, see docs/watch_frigate.md). Runs by itself once
 * started; reports one event per motion episode:
 *
 *     watch_start();
 *     if (watch_ready()) {
 *         watch_read(&ev);              // frame, changed tiles, bounding box
 *         ...                           // policy: snitch a picture, CAN message, ...
 *     }
 *
 * Tiles: VIS_WATCH_TILES_X x VIS_WATCH_TILES_Y of VIS_WATCH_TILE pixels.
 */
typedef struct {
    uint16_t frame;                    /* frame where the episode started              */
    uint8_t  tiles;                    /* changed tiles in that frame                  */
    uint8_t  x0, x1, y0, y1;           /* bounding box in tiles                         */
} watch_event_t;

typedef struct {
    uint8_t running, calibrating, active, ready, lost, changed;
} watch_status_t;

void     watch_start(void);           /* begin (calibrates first)                      */
void     watch_stop(void);
int      watch_ready(void);           /* an event is waiting                           */
void     watch_read(watch_event_t *ev);/* take it                                      */
void     watch_status(watch_status_t *st);
uint16_t watch_frame(void);           /* frame_id of the last frame seen (for fps)     */

#endif
