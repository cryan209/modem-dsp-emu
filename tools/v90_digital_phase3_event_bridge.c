/*
 * Stateful diagnostic V.90D Phase-3 event bridge.
 *
 * This is intentionally a narrow child-process seam: PCMU frames arrive on
 * stdin, the sibling p3 demodulator recognizes completed analogue training
 * segments, and the sibling digital V.90 transmitter emits PCMU frames on
 * stdout.  It does not claim to implement Phase 4 until CP events are wired.
 *
 * The bridge is opt-in and is built against the sibling checkout.  It exists
 * to replace the old frame-only wire surrogate with an explicit event/state
 * boundary that can be measured in the Eicon harness.
 */
#include "p3_demod.h"
#include "v90.h"
#include "v90_dil_presets.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>

#define FRAME 160
#define MAX_REPORTED_EVENTS 128
#define RESULT_TAIL_SYMBOLS 4096

typedef struct {
    int start_sample;
    p3_signal_type_t type;
} reported_event_t;

typedef struct {
    p3_demod_t demod;
    p3_result_t *result;
    v90_state_t *v90;
    int sample_offset;
    reported_event_t reported[MAX_REPORTED_EVENTS];
    int reported_count;
} bridge_t;

static int16_t pcmu_decode(uint8_t code)
{
    uint8_t v = (uint8_t)~code;
    int magnitude = (((v & 0x0f) << 1) + 33) << ((v >> 4) & 7);
    magnitude -= 33;
    return (int16_t)((v & 0x80) ? -magnitude : magnitude);
}

static void report_event(bridge_t *b, p3_signal_type_t type)
{
    v90_rx_event_t event = V90_RX_EVENT_NONE;

    switch (type) {
    case P3_SIGNAL_S:
    case P3_SIGNAL_S_BAR:
        if (v90_get_tx_phase(b->v90) != V90_TX_JD
                && v90_get_tx_phase(b->v90) != V90_TX_DIL)
            return;
        event = V90_RX_EVENT_S;
        break;
    case P3_SIGNAL_J:
        if (v90_get_tx_phase(b->v90) != V90_TX_WAIT_JA)
            return;
        event = V90_RX_EVENT_J;
        break;
    default:
        break;
    }
    if (event != V90_RX_EVENT_NONE
            && v90_handle_rx_event(b->v90, event))
        fprintf(stderr, "[v90d-event] %s accepted tx_phase=%d\n",
                v90_rx_event_name(event), (int)v90_get_tx_phase(b->v90));
}

static int bridge_init(bridge_t *b)
{
    v90_dil_desc_t dil;

    memset(b, 0, sizeof(*b));
    p3_demod_init(&b->demod, 4, P3_CARRIER_LOW, 8000);
    b->result = p3_result_alloc(32768, 4096);
    b->v90 = v90_init_data_pump(V90_LAW_ULAW);
    if (!b->result || !b->v90)
        return -1;
    memset(&dil, 0, sizeof(dil));
    if (!v90_dil_preset_load(V90_DIL_PRESET_DEFAULT_JA, &dil))
        return -1;
    v90_set_dil_descriptor(b->v90, &dil);
    v90_start_phase3(b->v90, 78);
    return 0;
}

static void bridge_free(bridge_t *b)
{
    if (b->v90)
        v90_free(b->v90);
    if (b->result)
        p3_result_free(b->result);
}

static int bridge_reset_if_requested(bridge_t *b, const char *reset_file,
                                     time_t *last_mtime)
{
    struct stat st;

    if (!reset_file || stat(reset_file, &st) != 0 || st.st_mtime == *last_mtime)
        return 0;
    bridge_free(b);
    if (bridge_init(b) != 0)
        return -1;
    *last_mtime = st.st_mtime;
    fprintf(stderr, "[v90d-event] reset at media boundary\n");
    return 1;
}

static void bridge_events(bridge_t *b)
{
    int count = p3_segment_symbols(b->result);
    for (int i = 0; i < count; i++) {
        const p3_segment_t *segment = &b->result->segments[i];
        int already_reported = 0;

        /* The segmenter rebuilds the complete list on every call.  Ignore
         * opaque/unstable segments and only publish each state-relevant
         * start/type pair once. */
        if (segment->type == P3_SIGNAL_UNKNOWN
                || segment->type == P3_SIGNAL_SILENCE
                || segment->type == P3_SIGNAL_PP
                || segment->type == P3_SIGNAL_TRN
                || segment->type == P3_SIGNAL_RU
                || segment->type == P3_SIGNAL_UR
                )
            continue;
        if (segment->type == P3_SIGNAL_S
                || segment->type == P3_SIGNAL_S_BAR
                || segment->type == P3_SIGNAL_J) {
            for (int j = 0; j < b->reported_count; j++) {
                if (b->reported[j].start_sample == segment->start_sample
                        && b->reported[j].type == segment->type) {
                    already_reported = 1;
                    break;
                }
            }
            if (already_reported)
                continue;
            report_event(b, segment->type);
            if (b->reported_count < MAX_REPORTED_EVENTS) {
                b->reported[b->reported_count].start_sample = segment->start_sample;
                b->reported[b->reported_count].type = segment->type;
                b->reported_count++;
            }
        }
    }
}

static void bridge_trim_result(bridge_t *b)
{
    p3_result_t *result = b->result;
    int keep = RESULT_TAIL_SYMBOLS;

    if (result->symbol_count <= keep * 2)
        return;
    memmove(result->symbols,
            result->symbols + result->symbol_count - keep,
            (size_t)keep * sizeof(*result->symbols));
    result->symbol_count = keep;
}

int main(int argc, char **argv)
{
    bridge_t bridge;
    uint8_t input[FRAME], output[FRAME];
    int16_t linear[FRAME];
    const char *reset_file = NULL;
    time_t reset_mtime = 0;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--reset-file") && i + 1 < argc)
            reset_file = argv[++i];
        else if (!strcmp(argv[i], "--stream"))
            continue;
        else {
            fprintf(stderr, "usage: %s [--stream] [--reset-file path]\n",
                    argv[0]);
            return 2;
        }
    }

    if (bridge_init(&bridge) != 0) {
        fprintf(stderr, "v90d event bridge initialization failed\n");
        bridge_free(&bridge);
        return 1;
    }
    while (fread(input, 1, FRAME, stdin) == FRAME) {
        if (bridge_reset_if_requested(&bridge, reset_file, &reset_mtime) < 0)
            break;
        for (int i = 0; i < FRAME; i++)
            linear[i] = pcmu_decode(input[i]);
        p3_demod_process(&bridge.demod, linear, FRAME,
                         bridge.sample_offset, bridge.result);
        bridge.sample_offset += FRAME;
        bridge_events(&bridge);
        bridge_trim_result(&bridge);
        if (v90_phase3_tx_codewords(bridge.v90, output, FRAME) != FRAME)
            break;
        if (fwrite(output, 1, FRAME, stdout) != FRAME)
            break;
        fflush(stdout);
    }
    fprintf(stderr, "[v90d-event] final tx_phase=%d complete=%d\n",
            (int)v90_get_tx_phase(bridge.v90),
            v90_training_complete(bridge.v90) ? 1 : 0);
    bridge_free(&bridge);
    return 0;
}
