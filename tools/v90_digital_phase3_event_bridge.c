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
#include "v90_cp_rx.h"
#include "v90_cp_live.h"
#include "v90_dil_presets.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <pthread.h>

#define FRAME 160
#define DATA_FRAME 6
#define DATA_BYTES 256
#define DATA_BITS (DATA_BYTES * 8)
/* The sideband producer and V.90D consumer run at different rates.  A
 * 64 Ki-bit queue overflowed during sustained PPP and discarded valid HDLC
 * bits.  Keep a bounded backlog large enough for a long TCP transfer. */
#define DATA_QUEUE_BITS (8 * 1024 * 1024)
#define SIDEBAND_MAGIC 0xA5CU
#define SIDEBAND_HEADER_SAMPLES 8
#define SIDEBAND_BITS_PER_SAMPLE 3
#define SIDEBAND_FRAME_BITS ((FRAME - SIDEBAND_HEADER_SAMPLES) * SIDEBAND_BITS_PER_SAMPLE)
#define MAX_REPORTED_EVENTS 128
#define RESULT_TAIL_SYMBOLS 2048
#define SEGMENT_SCAN_FRAMES 4
#define CP_LIVE_MAX_SAMPLES (60 * 8000 + 160)
#define CP_LIVE_FIRST_TRY_DELAY 800
#define CP_LIVE_SEARCH_TAIL 2000
#define CP_LIVE_RETRY_SAMPLES 320
#define CP_LIVE_MAX_ATTEMPTS 1024

typedef struct {
    int start_sample;
    p3_signal_type_t type;
    int last_phase;
    int accepted;
} reported_event_t;

typedef struct {
    p3_demod_t demod;
    p3_result_t *result;
    v90_state_t *v90;
    v90_cp_rx_t cp_rx;
    int sample_offset;
    int event_arm_sample;
    reported_event_t reported[MAX_REPORTED_EVENTS];
    int reported_count;
    int segment_scan_frames;
    int16_t live_samples[CP_LIVE_MAX_SAMPLES];
    int live_sample_count;
    int live_phase4_hint;
    int live_next_try;
    int live_attempts;
    int live_cp_accepted;
    int live_data_cp_accepted;
    int live_cp_done;
    int live_enabled;
    int connected_reported;
    int connected_sample;
    uint8_t data_frame[DATA_FRAME];
    int data_frame_pos;
    uint8_t last_codeword;
    int data_frames;
    v34_state_t *upstream_rx;
    uint8_t *tx_bits;
    unsigned tx_rd;
    unsigned tx_wr;
    unsigned tx_count;
    unsigned tx_consumed_frame;
    uint8_t *rx_bits;
    unsigned rx_rd;
    unsigned rx_wr;
    unsigned rx_count;
    int upstream_rx_started;
    int upstream_e_ones;
    int upstream_e_complete;
    int upstream_e_post_bits;
    int upstream_look_for_e;
    int upstream_prepared;
    int upstream_replayed_current_frame;
    int upstream_audio_delay;
    int upstream_prepare_at;
    int upstream_begin_at;
    int upstream_begin_pending;
    int sideband_enabled;
    unsigned sideband_frames;
    int baud_code;
    int high_carrier;
    int upstream_bps;
    pthread_mutex_t live_mutex;
    pthread_cond_t live_cond;
    pthread_t live_thread;
    int live_sync_initialized;
    int live_stop;
    int live_job;
    int live_worker_running;
    int live_job_count;
    int live_job_offset;
    int live_job_hint;
    int live_job_expected;
    int16_t *live_job_samples;
    int live_result_ready;
    int live_result_found;
    int live_result_expected;
    vpcm_cp_diag_t live_diag;
    v90_cp_live_meta_t live_meta;
} bridge_t;

static int bridge_begin_upstream_data(bridge_t *b)
{
    return v34_begin_rx_data(b->upstream_rx);
}

static void bridge_feed_upstream(bridge_t *b, const int16_t *samples,
                                 int count, int sample_start)
{
    int begin_at = b->upstream_begin_at;

    if (count <= 0)
        return;
    if (b->upstream_begin_pending && !b->upstream_rx_started
            && begin_at >= 0) {
        int before = begin_at <= sample_start ? 0 : begin_at - sample_start;

        if (begin_at >= sample_start + count)
            before = -1;
        if (before < 0)
            goto feed_all;

        if (before > 0)
            (void)v34_rx(b->upstream_rx, samples, before);
        if (bridge_begin_upstream_data(b) == 0) {
            b->upstream_rx_started = 1;
            b->upstream_begin_pending = 0;
            fprintf(stderr,
                    "[v90d-event] upstream E/B1 handover scheduled sample=%d\n",
                    begin_at);
        }
        (void)v34_rx(b->upstream_rx, samples + before, count - before);
        return;
    }
feed_all:
    (void)v34_rx(b->upstream_rx, samples, count);
}

static int bridge_get_bit(void *user_data)
{
    bridge_t *b = user_data;
    int bit = 1;

    if (b->tx_count) {
        bit = b->tx_bits[b->tx_rd];
        b->tx_rd = (b->tx_rd + 1U) % DATA_QUEUE_BITS;
        b->tx_count--;
    }
    b->tx_consumed_frame++;
    return bit;
}

static void bridge_put_bit(void *user_data, int bit)
{
    bridge_t *b = user_data;
    uint32_t rejected_before;

    if (bit != 0 && bit != 1)
        return;
    /* Let the recovered CP decoder qualify the V.34 receiver's timing/
     * carrier hypotheses.  This is control-plane work only: once the E/B1
     * handover has started, continuing to parse every DATA bit as CP both
     * wastes the media tick and can keep the rejected-frame search alive for
     * the duration of a long TCP transfer. */
    if (!b->upstream_rx_started) {
        rejected_before = b->cp_rx.rejected_frames;
        (void)v90_cp_rx_put_bit(&b->cp_rx, bit);
        if (b->cp_rx.rejected_frames != rejected_before)
            v34_reject_v90_phase4_hypothesis(b->upstream_rx);
    }
    /* §8.5.3: the upstream E handover is twenty recovered ones after CP.
     * v34_begin_rx_data() must be called at that recovered-bit boundary, not
     * when the independent downstream V.90 transmitter completes.  The
     * receive RRC needs one additional dibit of history, matching the sibling
     * coupled-session implementation. */
    if (!b->upstream_rx_started) {
        if (!b->upstream_look_for_e)
            return;
        if (!b->upstream_e_complete) {
            if (bit)
                b->upstream_e_ones++;
            else
                b->upstream_e_ones = 0;
            if (b->upstream_e_ones >= 20)
                b->upstream_e_complete = 1;
        } else {
            b->upstream_e_post_bits++;
        }
        if (b->upstream_e_complete && b->upstream_e_post_bits >= 2
                && b->upstream_prepared && !b->upstream_rx_started
                && bridge_begin_upstream_data(b) == 0) {
            b->upstream_rx_started = 1;
            fprintf(stderr, "[v90d-event] upstream E/B1 handover\n");
        }
        return;
    }
    if (b->rx_count >= DATA_QUEUE_BITS)
        return;
    b->rx_bits[b->rx_wr] = bit & 1;
    b->rx_wr = (b->rx_wr + 1U) % DATA_QUEUE_BITS;
    b->rx_count++;
}

static void bridge_put_data_bit(void *user_data, int bit)
{
    bridge_t *b = user_data;

    if (b->sideband_enabled)
        return;
    if ((bit != 0 && bit != 1) || b->rx_count >= DATA_QUEUE_BITS)
        return;
    b->rx_bits[b->rx_wr] = bit & 1;
    b->rx_wr = (b->rx_wr + 1U) % DATA_QUEUE_BITS;
    b->rx_count++;
}

static void bridge_extract_sideband(bridge_t *b, const uint8_t pcm[FRAME])
{
    uint32_t header = 0;
    unsigned count;

    if (!b->sideband_enabled)
        return;
    for (int i = 0; i < SIDEBAND_HEADER_SAMPLES; i++)
        header |= (uint32_t)(pcm[i] & 7U) << (3*i);
    if ((header & 0xFFFU) != SIDEBAND_MAGIC)
        return;
    count = (header >> 12) & 0xFFFU;
    if (count > SIDEBAND_FRAME_BITS)
        return;
    for (unsigned i = 0; i < count && b->rx_count < DATA_QUEUE_BITS; i++) {
        unsigned sample = SIDEBAND_HEADER_SAMPLES
                        + i / SIDEBAND_BITS_PER_SAMPLE;
        unsigned shift = i % SIDEBAND_BITS_PER_SAMPLE;

        b->rx_bits[b->rx_wr] = (pcm[sample] >> shift) & 1U;
        b->rx_wr = (b->rx_wr + 1U) % DATA_QUEUE_BITS;
        b->rx_count++;
    }
    b->sideband_frames++;
}

static void bridge_queue_input(bridge_t *b,
                               const uint8_t packed[DATA_BYTES], unsigned count)
{
    if (count > DATA_BITS)
        count = DATA_BITS;
    for (unsigned i = 0; i < count && b->tx_count < DATA_QUEUE_BITS; i++) {
        b->tx_bits[b->tx_wr] = (packed[i >> 3] >> (i & 7)) & 1U;
        b->tx_wr = (b->tx_wr + 1U) % DATA_QUEUE_BITS;
        b->tx_count++;
    }
}

static unsigned bridge_pack_output(bridge_t *b, uint8_t packed[DATA_BYTES])
{
    unsigned count = b->rx_count < DATA_BITS ? b->rx_count : DATA_BITS;

    memset(packed, 0, DATA_BYTES);
    for (unsigned i = 0; i < count; i++) {
        packed[i >> 3] |= b->rx_bits[b->rx_rd] << (i & 7);
        b->rx_rd = (b->rx_rd + 1U) % DATA_QUEUE_BITS;
    }
    b->rx_count -= count;
    return count;
}

static int16_t pcmu_decode(uint8_t code)
{
    uint8_t v = (uint8_t)~code;
    int magnitude = (((v & 0x0f) << 1) + 33) << ((v >> 4) & 7);
    magnitude -= 33;
    return (int16_t)((v & 0x80) ? -magnitude : magnitude);
}

static void bridge_cp_frame(void *user_data, const vpcm_cp_diag_t *diag)
{
    bridge_t *b = user_data;

    if (!b || !diag || !diag->valid)
        return;
    if (!v90_set_phase4_cp(b->v90, &diag->frame)) {
        fprintf(stderr, "[v90d-event] CP rejected by V90 state\n");
        return;
    }
    fprintf(stderr, "[v90d-event] CP accepted drn=%u compat=%d ack=%d\n",
            (unsigned)diag->frame.drn,
            diag->frame.v90_compatibility ? 1 : 0,
            diag->frame.acknowledge ? 1 : 0);
    /* This callback is driven by the streaming V.34 bit receiver and is
     * therefore close enough to wire time to catch the following E and B1.
     * The strict batch detector may confirm CP later, but its callback is too
     * late to start the ordinary fixed-window T/2 B1 capture. */
    b->upstream_look_for_e = 1;
    if (!b->upstream_prepared
            && v34_v90_prepare_upstream_data(
                   b->upstream_rx, b->baud_code, b->high_carrier,
                   b->upstream_bps, 0) == 0) {
        b->upstream_prepared = 1;
        fprintf(stderr,
                "[v90d-event] upstream receiver prepared by streaming CP\n");
    }
    (void)v90_handle_rx_event(b->v90, V90_RX_EVENT_CP_VALID);
}

static int report_event(bridge_t *b, p3_signal_type_t type)
{
    v90_rx_event_t event = V90_RX_EVENT_NONE;

    switch (type) {
    case P3_SIGNAL_S:
    case P3_SIGNAL_S_BAR:
        if (v90_get_tx_phase(b->v90) != V90_TX_JD
                && v90_get_tx_phase(b->v90) != V90_TX_DIL)
            return 0;
        event = V90_RX_EVENT_S;
        break;
    case P3_SIGNAL_J:
        if (v90_get_tx_phase(b->v90) != V90_TX_WAIT_JA)
            return 0;
        event = V90_RX_EVENT_J;
        break;
    default:
        break;
    }
    if (event != V90_RX_EVENT_NONE
            && v90_handle_rx_event(b->v90, event)) {
        fprintf(stderr, "[v90d-event] %s accepted tx_phase=%d\n",
                v90_rx_event_name(event), (int)v90_get_tx_phase(b->v90));
        return 1;
    }
    return 0;
}

static void *bridge_live_cp_worker(void *user_data)
{
    bridge_t *b = user_data;

    for (;;) {
        int16_t *samples;
        int sample_count;
        int sample_offset;
        int hint;
        int expected;
        vpcm_cp_diag_t diag;
        v90_cp_live_meta_t meta;
        int found;

        pthread_mutex_lock(&b->live_mutex);
        while (!b->live_stop && !b->live_job)
            pthread_cond_wait(&b->live_cond, &b->live_mutex);
        if (b->live_stop) {
            pthread_mutex_unlock(&b->live_mutex);
            break;
        }
        samples = b->live_job_samples;
        b->live_job_samples = NULL;
        sample_count = b->live_job_count;
        sample_offset = b->live_job_offset;
        hint = b->live_job_hint;
        expected = b->live_job_expected;
        b->live_job = 0;
        b->live_worker_running = 1;
        pthread_mutex_unlock(&b->live_mutex);

        memset(&diag, 0, sizeof(diag));
        memset(&meta, 0, sizeof(meta));
        found = samples && v90_cp_live_recover(
            samples, sample_count, hint, b->baud_code, expected, false,
            &diag, &meta);
        if (found) {
            meta.frame_sample += sample_offset;
            meta.last_sample += sample_offset;
        }
        free(samples);

        pthread_mutex_lock(&b->live_mutex);
        b->live_worker_running = 0;
        if (!b->live_stop) {
            b->live_result_found = found;
            b->live_result_expected = expected;
            if (found) {
                b->live_diag = diag;
                b->live_meta = meta;
            }
            b->live_result_ready = 1;
        }
        pthread_mutex_unlock(&b->live_mutex);
    }
    return NULL;
}

static int bridge_live_cp_start(bridge_t *b)
{
    if (!b->live_enabled)
        return 0;
    if (pthread_mutex_init(&b->live_mutex, NULL) != 0)
        return -1;
    if (pthread_cond_init(&b->live_cond, NULL) != 0) {
        pthread_mutex_destroy(&b->live_mutex);
        return -1;
    }
    b->live_sync_initialized = 1;
    if (pthread_create(&b->live_thread, NULL, bridge_live_cp_worker, b) != 0) {
        pthread_cond_destroy(&b->live_cond);
        pthread_mutex_destroy(&b->live_mutex);
        b->live_sync_initialized = 0;
        return -1;
    }
    return 0;
}

static void bridge_live_cp_stop(bridge_t *b)
{
    if (!b->live_sync_initialized)
        return;
    pthread_mutex_lock(&b->live_mutex);
    b->live_stop = 1;
    pthread_cond_signal(&b->live_cond);
    pthread_mutex_unlock(&b->live_mutex);
    pthread_join(b->live_thread, NULL);
    free(b->live_job_samples);
    b->live_job_samples = NULL;
    pthread_cond_destroy(&b->live_cond);
    pthread_mutex_destroy(&b->live_mutex);
    b->live_sync_initialized = 0;
}

static void bridge_live_cp_apply(bridge_t *b)
{
    vpcm_cp_diag_t diag;
    v90_cp_live_meta_t meta;
    int expected;
    int found;

    if (!b->live_enabled)
        return;
    pthread_mutex_lock(&b->live_mutex);
    if (!b->live_result_ready) {
        pthread_mutex_unlock(&b->live_mutex);
        return;
    }
    found = b->live_result_found;
    expected = b->live_result_expected;
    diag = b->live_diag;
    meta = b->live_meta;
    b->live_result_ready = 0;
    pthread_mutex_unlock(&b->live_mutex);
    if (!found)
        return;
    /* CP is repeated until MP' is acknowledged.  Reapplying the same plain
     * data-mode CP every 40 ms restarts no useful state, floods the realtime
     * log, and obscures the later CP'.  Continue searching, but only deliver
     * the first plain CP and the first acknowledged CP'. */
    if (expected && b->live_data_cp_accepted && !diag.frame.acknowledge)
        return;
    if (!v90_set_phase4_cp(b->v90, &diag.frame))
        return;
    b->live_cp_accepted = 1;
        if (expected) {
            b->live_data_cp_accepted = 1;
            b->upstream_look_for_e = 1;
            b->upstream_begin_pending = 1;
            if (!b->upstream_prepared
                    && v34_v90_prepare_upstream_data(
                           b->upstream_rx, b->baud_code,
                           b->high_carrier, b->upstream_bps, 0) == 0) {
                b->upstream_prepared = 1;
                fprintf(stderr,
                        "[v90d-event] upstream receiver prepared by strict CP\n");
            }
        /* Keep control events on the same clock as the optionally delayed
         * V.34 audio.  Applying this retune immediately would move the
         * delayed receiver into CP while it was still consuming Phase 3. */
        if (!b->upstream_prepared && b->upstream_prepare_at < 0)
            b->upstream_prepare_at = meta.last_sample;
        if (diag.frame.acknowledge) {
            b->live_cp_done = 1;
            /* Apply the completed strict result before the current audio
             * frame is handed to v34_rx().  The worker normally completes
             * near the CP'/E boundary; applying it after v34_rx() discarded
             * another 20 ms, longer than the useful B1 timing margin. */
            if (b->upstream_audio_delay > 0) {
                b->upstream_begin_pending = 1;
            } else {
                int replay_start = meta.last_sample;

                /* The strict worker is asynchronous, so the live clock has
                 * normally already consumed E and part of B1 by the time the
                 * result is applied.  Enter DATA now, then replay the
                 * buffered CP->E->B1 interval through the receiver.  This
                 * gives v34_begin_rx_data() the same B1-relative history as
                 * the synchronous CP-bit callback, without anchoring it to a
                 * stale wall-clock sample. */
                if (!b->upstream_prepared
                        && v34_v90_prepare_upstream_data(
                               b->upstream_rx, b->baud_code,
                               b->high_carrier, b->upstream_bps, 0) == 0)
                    b->upstream_prepared = 1;
                if (b->upstream_prepared && !b->upstream_rx_started) {
                    if (replay_start < 0)
                        replay_start = 0;
                    if (replay_start < b->live_sample_count
                            && bridge_begin_upstream_data(b) == 0) {
                        b->upstream_rx_started = 1;
                        b->upstream_begin_pending = 0;
                        b->upstream_look_for_e = 0;
                        (void)v34_rx(b->upstream_rx,
                                     b->live_samples + replay_start,
                                     b->live_sample_count - replay_start);
                        b->upstream_replayed_current_frame = 1;
                        fprintf(stderr,
                                "[v90d-event] upstream E/B1 handover "
                                "replayed from CP sample=%d through=%d\n",
                                replay_start, b->live_sample_count);
                    }
                }
            }
        }
    }
    (void)v90_handle_rx_event(b->v90, V90_RX_EVENT_CP_VALID);
    fprintf(stderr,
            "[v90d-event] live %s accepted sample=%d carrier=%d "
            "timing=%d step=%d drn=%u vote=%d/%d%% response-lag=%d\n",
            expected ? "CP" : "CPt", meta.frame_sample,
            meta.carrier_sel, meta.timing_index, meta.carrier_step,
            (unsigned)diag.frame.drn, meta.voted_frames,
            meta.agreement_pct, b->live_sample_count - meta.last_sample);
}

static void bridge_live_cp_try(bridge_t *b)
{
    int expected_compatibility;
    int16_t *snapshot;
    int sample_count;
    int sample_offset;

    if (!b->live_enabled || b->live_cp_done || b->live_phase4_hint < 0
            || b->live_attempts >= CP_LIVE_MAX_ATTEMPTS
            || b->live_sample_count < b->live_next_try)
        return;
    expected_compatibility = b->live_cp_accepted ? 1 : 0;
    pthread_mutex_lock(&b->live_mutex);
    if (b->live_job || b->live_worker_running || b->live_result_ready) {
        pthread_mutex_unlock(&b->live_mutex);
        return;
    }
    sample_offset = b->live_sample_count - CP_LIVE_SEARCH_TAIL;
    if (sample_offset < 0)
        sample_offset = 0;
    sample_count = b->live_sample_count - sample_offset;
    snapshot = malloc((size_t)sample_count * sizeof(*snapshot));
    if (!snapshot) {
        pthread_mutex_unlock(&b->live_mutex);
        return;
    }
    memcpy(snapshot, b->live_samples + sample_offset,
           (size_t)sample_count * sizeof(*snapshot));
    b->live_job_samples = snapshot;
    b->live_job_count = sample_count;
    b->live_job_offset = sample_offset;
    /* CPt/CP are self-contained, CRC-protected frames.  Searching a recent
     * quarter-second tail avoids repeatedly demodulating the entire Phase-4
     * history; hint 1 makes the strict direct receiver inspect that tail. */
    b->live_job_hint = 1;
    b->live_job_expected = expected_compatibility;
    b->live_job = 1;
    b->live_attempts++;
    b->live_next_try = b->live_sample_count + CP_LIVE_RETRY_SAMPLES;
    pthread_cond_signal(&b->live_cond);
    pthread_mutex_unlock(&b->live_mutex);
}

static void bridge_note_phase4(bridge_t *b)
{
    v90_tx_phase_t phase;

    if (!b->live_enabled || b->live_phase4_hint >= 0)
        return;
    phase = v90_get_tx_phase(b->v90);
    if (phase >= V90_TX_RI && phase <= V90_TX_DATA) {
        b->live_phase4_hint = b->live_sample_count;
        /* Start early and retry cheaply. Different analogue peers enter CPt
         * at materially different offsets from the local Ri transition. */
        b->live_next_try = b->live_sample_count + CP_LIVE_FIRST_TRY_DELAY;
        fprintf(stderr, "[v90d-event] live CP anchor sample=%d phase=%d\n",
                b->live_phase4_hint, (int)phase);
    }
}

static int bridge_init(bridge_t *b)
{
    v90_dil_desc_t dil;
    int baud_code = P3_BAUD_3200;
    int carrier = P3_CARRIER_LOW;
    const char *value;

    uint8_t *tx_bits = b->tx_bits;
    uint8_t *rx_bits = b->rx_bits;
    memset(b, 0, sizeof(*b));
    b->tx_bits = tx_bits ? tx_bits : calloc(DATA_QUEUE_BITS, 1);
    b->rx_bits = rx_bits ? rx_bits : calloc(DATA_QUEUE_BITS, 1);
    if (!b->tx_bits || !b->rx_bits)
        return -1;
    b->upstream_prepare_at = -1;
    b->upstream_begin_at = -1;
    value = getenv("EICON_V90D_BRIDGE_BAUD_CODE");
    if (value && *value)
        baud_code = atoi(value);
    value = getenv("EICON_V90D_BRIDGE_CARRIER");
    if (value && *value)
        carrier = atoi(value);
    if (baud_code < 0 || baud_code >= P3_BAUD_COUNT)
        baud_code = P3_BAUD_3200;
    if (carrier != P3_CARRIER_LOW && carrier != P3_CARRIER_HIGH)
        carrier = P3_CARRIER_LOW;
    value = getenv("EICON_V90D_BRIDGE_EVENT_ARM_SAMPLES");
    if (value && *value)
        b->event_arm_sample = atoi(value);
    if (b->event_arm_sample < 0)
        b->event_arm_sample = 0;
    p3_demod_init(&b->demod, baud_code, carrier, 8000);
    b->baud_code = baud_code;
    b->high_carrier = carrier == P3_CARRIER_HIGH;
    value = getenv("EICON_V90D_BRIDGE_UPSTREAM_BPS");
    b->upstream_bps = value && *value ? atoi(value) : 31200;
    if (b->upstream_bps < 4800 || b->upstream_bps > 33600
            || (b->upstream_bps % 2400) != 0)
        b->upstream_bps = 31200;
    value = getenv("EICON_V90D_BRIDGE_UPSTREAM_DELAY_SAMPLES");
    b->upstream_audio_delay = value && *value ? atoi(value) : 0;
    if (b->upstream_audio_delay < 0
            || b->upstream_audio_delay > CP_LIVE_SEARCH_TAIL * 2)
        b->upstream_audio_delay = 0;
    b->live_enabled = getenv("EICON_V90D_BRIDGE_CP_LIVE")
        && atoi(getenv("EICON_V90D_BRIDGE_CP_LIVE")) != 0;
    b->sideband_enabled = getenv("EICON_V90D_DATA_SIDEBAND")
        && atoi(getenv("EICON_V90D_DATA_SIDEBAND")) != 0;
    b->live_phase4_hint = -1;
    b->data_frame_pos = DATA_FRAME;
    b->last_codeword = 0xff;
    b->result = p3_result_alloc(32768, 4096);
    /* The analogue bridge selects the highest rate in the MP mask: 31,200
     * bit/s (13 x 2400).  Preparing this receiver at 28,800 produced a live
     * bit stream with ~56% BER even though B1 acquired. */
    b->v90 = v90_init(3200, 31200, false, V90_LAW_ULAW,
                      bridge_get_bit, b, bridge_put_bit, b);
    if (!b->result || !b->v90)
        return -1;
    b->upstream_rx = v90_get_v34(b->v90);
    if (getenv("EICON_V90D_BRIDGE_V34_LOG")
            && atoi(getenv("EICON_V90D_BRIDGE_V34_LOG")) != 0) {
        logging_state_t *log = v34_get_logging_state(b->upstream_rx);

        if (log != NULL)
            span_log_set_level(log, SPAN_LOG_SHOW_SEVERITY
                                    | SPAN_LOG_SHOW_PROTOCOL | SPAN_LOG_FLOW);
    }
    v34_set_put_bit(b->upstream_rx, bridge_put_data_bit, b);
    v34_set_put_phase4_bit(b->upstream_rx, bridge_put_bit, b);
    v34_force_v90_phase4_cp_rx(b->upstream_rx);
    memset(&dil, 0, sizeof(dil));
    if (!v90_dil_preset_load(V90_DIL_PRESET_DEFAULT_JA, &dil))
        return -1;
    v90_set_dil_descriptor(b->v90, &dil);
    v90_start_phase3(b->v90, 78);
    v90_cp_rx_init(&b->cp_rx, 4, false, bridge_cp_frame, b);
    if (bridge_live_cp_start(b) != 0)
        return -1;
    fprintf(stderr, "[v90d-event] demod baud_code=%d carrier=%s\n",
            baud_code, carrier == P3_CARRIER_HIGH ? "high" : "low");
    if (b->event_arm_sample)
        fprintf(stderr, "[v90d-event] ignoring segments that start before "
                "sample %d\n", b->event_arm_sample);
    if (b->live_enabled)
        fprintf(stderr, "[v90d-event] strict batch CP recovery enabled\n");
    return 0;
}

static void bridge_free(bridge_t *b)
{
    bridge_live_cp_stop(b);
    if (b->v90)
        v90_free(b->v90);
    if (b->result)
        p3_result_free(b->result);
    free(b->tx_bits);
    free(b->rx_bits);
    b->tx_bits = b->rx_bits = NULL;
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
        int observed = -1;
        int phase;

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
        if (segment->start_sample < b->event_arm_sample)
            continue;
        if (segment->type == P3_SIGNAL_S
                || segment->type == P3_SIGNAL_S_BAR
                || segment->type == P3_SIGNAL_J) {
            for (int j = 0; j < b->reported_count; j++) {
                if (b->reported[j].start_sample == segment->start_sample
                        && b->reported[j].type == segment->type) {
                    observed = j;
                    break;
                }
            }
            phase = (int)v90_get_tx_phase(b->v90);
            if (observed >= 0
                    && (b->reported[observed].accepted
                        || b->reported[observed].last_phase == phase))
                continue;
            /* A segment can be detected before the transmitter reaches the
             * phase that consumes it.  Only mark it reported after the event
             * is accepted; otherwise a one-shot S/J result is lost forever
             * on a small timing skew.  A rejection is retried after the TX
             * phase changes, but not on every 20 ms frame in the same phase:
             * repeated full-history classification and identical rejected
             * events previously pushed the live media tick past one second. */
            if (observed < 0 && b->reported_count < MAX_REPORTED_EVENTS) {
                observed = b->reported_count++;
                b->reported[observed].start_sample = segment->start_sample;
                b->reported[observed].type = segment->type;
            }
            if (observed >= 0) {
                b->reported[observed].last_phase = phase;
                b->reported[observed].accepted =
                    report_event(b, segment->type);
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

static int bridge_tx_codewords(bridge_t *b, uint8_t *output, int count)
{
    for (int i = 0; i < count; i++) {
        if (!v90_training_complete(b->v90)) {
            if (v90_phase3_tx_codewords(b->v90, output + i, 1) != 1) {
                /* A transient short read must not make the child exit: the
                 * endpoint would then keep its SIP call alive while sending
                 * no modem audio, which strands LAPM/TCP.  Preserve the last
                 * valid codeword until the sibling can produce again. */
                for (; i < count; i++)
                    output[i] = b->last_codeword;
                return count;
            }
            b->last_codeword = output[i];
            continue;
        }
        if (b->data_frame_pos >= DATA_FRAME) {
            uint8_t data[8] = {0};
            int needed = v90_data_input_bytes_needed(b->v90);
            int consumed = 0;

            if (needed > (int)sizeof(data))
                needed = sizeof(data);
            for (int byte = 0; byte < needed; byte++)
                for (int bit = 0; bit < 8; bit++)
                    data[byte] |= bridge_get_bit(b) << bit;
            if (v90_tx_data_frame_codewords(b->v90, b->data_frame,
                                            data, needed, &consumed, true)
                    != DATA_FRAME) {
                for (; i < count; i++)
                    output[i] = b->last_codeword;
                return count;
            }
            b->data_frame_pos = 0;
            b->data_frames++;
        }
        output[i] = b->data_frame[b->data_frame_pos++];
        b->last_codeword = output[i];
    }
    return count;
}

int main(int argc, char **argv)
{
    bridge_t bridge = {0};
    uint8_t input[FRAME], output[FRAME];
    int16_t linear[FRAME];
    const char *reset_file = NULL;
    int data_stream = 0;
    time_t reset_mtime = 0;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--reset-file") && i + 1 < argc)
            reset_file = argv[++i];
        else if (!strcmp(argv[i], "--stream"))
            continue;
        else if (!strcmp(argv[i], "--data-stream"))
            data_stream = 1;
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
        uint8_t input_bits[DATA_BYTES], output_bits[DATA_BYTES];
        if (data_stream) {
            uint8_t header[4];
            unsigned input_count;
            if (fread(header, 1, sizeof(header), stdin) != sizeof(header)
                    || fread(input_bits, 1, sizeof(input_bits), stdin)
                           != sizeof(input_bits))
                break;
            input_count = (unsigned)header[0] | ((unsigned)header[1] << 8);
            bridge_queue_input(&bridge, input_bits, input_count);
            bridge.tx_consumed_frame = 0;
        }
        if (bridge_reset_if_requested(&bridge, reset_file, &reset_mtime) < 0)
            break;
        bridge_extract_sideband(&bridge, input);
        for (int i = 0; i < FRAME; i++)
            linear[i] = pcmu_decode(input[i]);
        if (bridge.live_enabled && !bridge.connected_reported) {
            int copy = FRAME;
            if (bridge.live_sample_count + copy > CP_LIVE_MAX_SAMPLES)
                copy = CP_LIVE_MAX_SAMPLES - bridge.live_sample_count;
            if (copy > 0) {
                pthread_mutex_lock(&bridge.live_mutex);
                memcpy(bridge.live_samples + bridge.live_sample_count,
                       linear, (size_t)copy * sizeof(linear[0]));
                bridge.live_sample_count += copy;
                pthread_mutex_unlock(&bridge.live_mutex);
            }
        }
        if (!bridge.connected_reported)
            p3_demod_process(&bridge.demod, linear, FRAME,
                             bridge.sample_offset, bridge.result);
        /* Consume a worker result before this frame reaches the V.34
         * receiver.  B1 is short enough that the old post-receive ordering
         * systematically armed the fixed T/2 capture one frame late. */
        if (!bridge.connected_reported)
            bridge_live_cp_apply(&bridge);
        /* The V.90 upstream receiver's T/3 history is filled before the E/B1
         * handover.  Feeding only after v34_begin_rx_data() discards the very
         * history its B1 correlator searches and produces no payload bits. */
        if (bridge.upstream_replayed_current_frame)
            bridge.upstream_replayed_current_frame = 0;
        else if (bridge.upstream_audio_delay > 0) {
            int delayed_at = bridge.sample_offset
                           - bridge.upstream_audio_delay;
            int16_t delayed[FRAME];

            if (!bridge.upstream_prepared
                    && bridge.upstream_prepare_at >= 0
                    && delayed_at + FRAME >= bridge.upstream_prepare_at
                    && v34_v90_prepare_upstream_data(
                           bridge.upstream_rx, bridge.baud_code,
                           bridge.high_carrier, bridge.upstream_bps, 0) == 0) {
                bridge.upstream_prepared = 1;
                fprintf(stderr,
                        "[v90d-event] upstream receiver prepared on delayed CP clock at %d\n",
                        delayed_at);
            }
            if (bridge.upstream_begin_pending && bridge.upstream_prepared
                    && !bridge.upstream_rx_started
                    && bridge_begin_upstream_data(&bridge) == 0) {
                bridge.upstream_rx_started = 1;
                bridge.upstream_begin_pending = 0;
                fprintf(stderr,
                        "[v90d-event] upstream B1 search armed on delayed clock at %d\n",
                        delayed_at);
            }

            for (int i = 0; i < FRAME; i++) {
                int at = delayed_at + i;

                delayed[i] = (at >= 0 && at < bridge.live_sample_count)
                    ? bridge.live_samples[at] : 0;
            }
            (void)v34_rx(bridge.upstream_rx, delayed, FRAME);
        }
        else
            bridge_feed_upstream(&bridge, linear, FRAME,
                                 bridge.sample_offset);
        bridge.sample_offset += FRAME;
        if (!bridge.connected_reported
                && ++bridge.segment_scan_frames >= SEGMENT_SCAN_FRAMES) {
            bridge.segment_scan_frames = 0;
            bridge_events(&bridge);
        }
        if (!bridge.connected_reported) {
            bridge_note_phase4(&bridge);
            bridge_live_cp_try(&bridge);
        }
        /* The public training-complete bit is not asserted on every V.90D
         * path, but the TX phase is authoritative once it reaches DATA.
         * Continuing the phase-3 demodulator and segment scanner for the
         * entire PPP transfer makes the answerer progressively host-bound.
         * Those classifiers are setup-only; the V.34 upstream receiver and
         * phase-4 codeword path remain active below. */
        if (!bridge.connected_reported
                && (v90_training_complete(bridge.v90)
                    || v90_get_tx_phase(bridge.v90) >= V90_TX_DATA)) {
            bridge.connected_reported = 1;
            bridge.connected_sample = bridge.sample_offset;
            fprintf(stderr,
                    "[v90d-event] CONNECTED training-complete sample=%d\n",
                    bridge.connected_sample);
        }
        bridge_trim_result(&bridge);
        if (bridge_tx_codewords(&bridge, output, FRAME) != FRAME)
            break;
        if (fwrite(output, 1, FRAME, stdout) != FRAME)
            break;
        if (data_stream) {
            unsigned received = bridge_pack_output(&bridge, output_bits);
            if (v90_training_complete(bridge.v90))
                received |= 0x8000U;
            unsigned consumed = bridge.tx_consumed_frame;
            uint8_t header[4] = {
                consumed & 0xff, (consumed >> 8) & 0xff,
                received & 0xff, (received >> 8) & 0xff
            };
            if (fwrite(header, 1, sizeof(header), stdout) != sizeof(header)
                    || fwrite(output_bits, 1, sizeof(output_bits), stdout)
                           != sizeof(output_bits))
                break;
        }
        fflush(stdout);
    }
    fprintf(stderr,
            "[v90d-event] final tx_phase=%d complete=%d data_samples=%d "
            "data_frames=%d sideband_frames=%u\n",
            (int)v90_get_tx_phase(bridge.v90),
            v90_training_complete(bridge.v90) ? 1 : 0,
            bridge.connected_reported
                ? bridge.sample_offset - bridge.connected_sample : 0,
            bridge.data_frames, bridge.sideband_frames);
    bridge_free(&bridge);
    return 0;
}
