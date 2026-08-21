/*
 * Small ABI-stable seam for a future Eicon V.90 peer adapter.
 *
 * The sibling p3_demod API keeps its state and result layout private to the
 * sibling tree.  This wrapper exposes only incremental symbol decisions and
 * detected segments, so the Eicon media process does not need to mirror that
 * large struct or silently substitute a foreign wire protocol.
 *
 * Build (from the sibling checkout) into a temporary diagnostic library:
 *
 *   cc -dynamiclib -O2 -I. -I/Users/scottcryan/v90modem \
 *      -I/Users/scottcryan/v90modem/spandsp-master/src \
 *      tools/v90_stream_event_bridge.c /Users/scottcryan/v90modem/p3_demod.c \
 *      -o /private/tmp/libv90_stream_event_bridge.dylib -lm
 */

#include "p3_demod.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    int sample_index;
    int dibit;
    int bit0;
    int bit1;
    float re;
    float im;
    float magnitude;
} v90_bridge_symbol_t;

typedef struct {
    int type;
    int start_symbol;
    int length;
    int start_sample;
    int end_sample;
    float confidence;
} v90_bridge_segment_t;

typedef struct {
    p3_demod_t demod;
    p3_result_t *result;
    int emitted_symbols;
} v90_stream_bridge_t;

v90_stream_bridge_t *v90_stream_bridge_create(int baud_code, int carrier,
                                               int sample_rate)
{
    v90_stream_bridge_t *bridge = calloc(1, sizeof(*bridge));
    if (!bridge)
        return NULL;
    p3_demod_init(&bridge->demod, baud_code, carrier, sample_rate);
    bridge->result = p3_result_alloc(32768, 4096);
    if (!bridge->result) {
        free(bridge);
        return NULL;
    }
    return bridge;
}

void v90_stream_bridge_destroy(v90_stream_bridge_t *bridge)
{
    if (!bridge)
        return;
    p3_result_free(bridge->result);
    free(bridge);
}

void v90_stream_bridge_reset(v90_stream_bridge_t *bridge)
{
    if (!bridge)
        return;
    p3_demod_reset(&bridge->demod);
    if (bridge->result) {
        bridge->result->symbol_count = 0;
        bridge->result->segment_count = 0;
    }
    bridge->emitted_symbols = 0;
}

/* Returns the number of newly emitted symbols and copies them to `out`. */
int v90_stream_bridge_process(v90_stream_bridge_t *bridge,
                              const int16_t *samples, int sample_count,
                              int sample_offset,
                              v90_bridge_symbol_t *out, int out_capacity)
{
    int before;
    int produced;
    int count;
    if (!bridge || !bridge->result || !samples || !out || out_capacity <= 0)
        return 0;
    before = bridge->result->symbol_count;
    produced = p3_demod_process(&bridge->demod, samples, sample_count,
                                sample_offset, bridge->result);
    (void)produced;
    count = bridge->result->symbol_count - before;
    if (count > out_capacity)
        count = out_capacity;
    for (int i = 0; i < count; i++) {
        const p3_symbol_t *src = &bridge->result->symbols[before + i];
        out[i].sample_index = src->sample_index;
        out[i].dibit = src->dibit;
        out[i].bit0 = src->bit0;
        out[i].bit1 = src->bit1;
        out[i].re = src->re;
        out[i].im = src->im;
        out[i].magnitude = src->magnitude;
    }
    bridge->emitted_symbols += count;
    return count;
}

/* Re-run the sibling's segment detector over the accumulated stream. */
int v90_stream_bridge_segments(v90_stream_bridge_t *bridge,
                               v90_bridge_segment_t *out, int out_capacity)
{
    int count;
    if (!bridge || !bridge->result || !out || out_capacity <= 0)
        return 0;
    count = p3_segment_symbols(bridge->result);
    if (count > out_capacity)
        count = out_capacity;
    for (int i = 0; i < count; i++) {
        const p3_segment_t *src = &bridge->result->segments[i];
        out[i].type = src->type;
        out[i].start_symbol = src->start_symbol;
        out[i].length = src->length;
        out[i].start_sample = src->start_sample;
        out[i].end_sample = src->end_sample;
        out[i].confidence = src->confidence;
    }
    return count;
}
