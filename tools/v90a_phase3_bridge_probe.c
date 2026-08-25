/*
 * Exercise the sibling's coupled V.90A Phase-3 state machine against a PCMU
 * downstream capture and optionally write the generated upstream PCMU.
 *
 * The probe is deliberately offline. It establishes whether the sibling
 * event/state translator can consume the native V90D waveform before it is
 * placed behind the Eicon media boundary. In --stream mode,
 * EICON_V90A_PHASE3_START_S can delay initialization until a synchronized
 * Phase-3 boundary; this is a diagnostic timing control, not a wall-clock
 * protocol implementation.
 */
#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <string.h>

#include "/Users/scottcryan/v90modem/v90_analogue_phase3.h"
#include "/Users/scottcryan/v90modem/v90_dil_presets.h"

#define DATA_BYTES 256
#define DATA_BITS (DATA_BYTES * 8)
#define DATA_QUEUE_BITS (64 * 1024)
#define SIDEBAND_MAGIC 0xA5CU
#define SIDEBAND_HEADER_SAMPLES 8
#define SIDEBAND_FRAME_BITS ((160 - SIDEBAND_HEADER_SAMPLES) * 3)

static uint8_t pcmu_encode(int sample)
{
    /* The bridge receives signed 16-bit samples from the sibling engine.
     * Use the same ITU-T G.711 u-law mapping as the harness codec: the old
     * +33/8159 form treated these values as already right-shifted 14-bit
     * samples and changed both the segment and mantissa on normal modem
     * amplitudes. */
    int sign = sample < 0 ? 0x80 : 0;
    int magnitude = sample < 0 ? -sample : sample;
    int exponent;
    int mantissa;

    if (magnitude > 32635) magnitude = 32635;
    magnitude += 0x84;
    exponent = 7;
    for (int mask = 0x4000; exponent > 0 && !(magnitude & mask);
         mask >>= 1)
        exponent--;
    mantissa = (magnitude >> (exponent + 3)) & 0x0f;
    return (uint8_t)~(sign | (exponent << 4) | mantissa);
}

typedef struct {
    uint64_t bits;
    uint8_t queue[DATA_QUEUE_BITS];
    unsigned rd;
    unsigned wr;
    unsigned count;
    unsigned consumed_frame;
} idle_bit_source_t;

static int get_idle_bit(void *user_data)
{
    idle_bit_source_t *source = user_data;
    int bit = 1;

    if (source->count) {
        bit = source->queue[source->rd];
        source->rd = (source->rd + 1U) % DATA_QUEUE_BITS;
        source->count--;
    }
    source->bits++;
    source->consumed_frame++;
    return bit;
}

static void get_zero_symbol(void *user_data, float *re, float *im)
{
    (void)user_data;
    *re = 0.0f;
    *im = 0.0f;
}

static void queue_input_bits(idle_bit_source_t *source,
                             const uint8_t packed[DATA_BYTES], unsigned count)
{
    if (count > DATA_BITS)
        count = DATA_BITS;
    for (unsigned i = 0; i < count && source->count < DATA_QUEUE_BITS; i++) {
        source->queue[source->wr] = (packed[i >> 3] >> (i & 7)) & 1U;
        source->wr = (source->wr + 1U) % DATA_QUEUE_BITS;
        source->count++;
    }
}

static void embed_sideband(uint8_t pcm[160], idle_bit_source_t *source,
                           unsigned *sequence)
{
    unsigned count = source->count < SIDEBAND_FRAME_BITS
                   ? source->count : SIDEBAND_FRAME_BITS;
    uint32_t header = SIDEBAND_MAGIC | (count << 12)
                    | ((*sequence & 7U) << 21);

    for (int i = 0; i < SIDEBAND_HEADER_SAMPLES; i++)
        pcm[i] = (uint8_t)((pcm[i] & 0xF8U) | ((header >> (3*i)) & 7U));
    for (unsigned i = 0; i < count; i++) {
        unsigned sample = SIDEBAND_HEADER_SAMPLES + i/3;
        unsigned shift = i % 3;
        unsigned bit = source->queue[source->rd];

        source->rd = (source->rd + 1U) % DATA_QUEUE_BITS;
        source->count--;
        pcm[sample] = (uint8_t)((pcm[sample] & ~(1U << shift))
                                | (bit << shift));
    }
    (*sequence)++;
}

static unsigned pack_output_bits(v90_analogue_phase3_t *phase3,
                                 uint8_t packed[DATA_BYTES])
{
    uint8_t bits[DATA_BITS];
    int count;

    memset(packed, 0, DATA_BYTES);
    if (!phase3)
        return 0;
    count = v90_analogue_phase3_get_data_bits(phase3, bits, DATA_BITS);
    if (count < 0)
        return 0;
    for (int i = 0; i < count; i++)
        packed[i >> 3] |= (bits[i] & 1U) << (i & 7);
    return (unsigned)count;
}

static const char *tx_name(v90_analogue_tx_stage_t stage)
{
    return v90_analogue_tx_stage_name(stage);
}

static v90_analogue_phase3_t *new_phase3(v34_state_t **v34_out,
                                         idle_bit_source_t *source)
{
    v90_dil_desc_t dil;
    v90_analogue_phase3_config_t cfg;
    const char *profile = getenv("EICON_V90A_PHASE3_DIL_PRESET");
    const char *high_carrier = getenv("EICON_V90A_PHASE3_HIGH_CARRIER");
    const char *max_tx_dbm0 = getenv("EICON_V90A_PHASE3_MAX_TX_DBM0");
    const char *upstream_max_n = getenv("EICON_V90A_PHASE3_UPSTREAM_MAX_N");
    v90_dil_preset_t preset = V90_DIL_PRESET_DEFAULT_JA;

    memset(&dil, 0, sizeof(dil));
    if (profile && (!strcmp(profile, "none")
                    || !strcmp(profile, "zero"))) {
        /* V.90 N=0 still has a Ja descriptor on the wire, but it contains
         * no DIL segments.  The analogue-role implementation represents that
         * explicitly with n == 0; do not substitute the default probe profile
         * when an Eicon V90A peer advertises N=0. */
        memset(&dil, 0, sizeof(dil));
    } else if (profile && (!strcmp(profile, "courier")
                    || !strcmp(profile, "card")))
        preset = V90_DIL_PRESET_COURIER_STYLE;
    else if (profile && !strcmp(profile, "measurement"))
        preset = V90_DIL_PRESET_MEASUREMENT;
    if (profile && (!strcmp(profile, "none")
                    || !strcmp(profile, "zero"))) {
        /* already initialized above */
    } else if (!v90_dil_preset_load(preset, &dil))
        return NULL;
    memset(&cfg, 0, sizeof(cfg));
    cfg.law = V90_LAW_ULAW;
    cfg.baud_rate_code = 4;
    /* The live sibling derives these from INFO1d/INFO0d.  Keep the historical
     * low-carrier, uncapped defaults, but expose the two peer capabilities for
     * controlled loopback A/Bs rather than silently baking in a guess. */
    cfg.high_carrier = high_carrier && atoi(high_carrier) != 0;
    cfg.u_info = 78;
    cfg.dil = dil;
    cfg.dil_coverage = 1.0;
    cfg.digital_max_tx_dbm0 = max_tx_dbm0 && *max_tx_dbm0
                            ? strtod(max_tx_dbm0, NULL) : 0.0;
    cfg.upstream_max_n = upstream_max_n && *upstream_max_n
                       ? atoi(upstream_max_n) : 0;
    memset(source, 0, sizeof(*source));
    cfg.v34 = v34_init(NULL, 3200, 28800, true, true,
                       get_idle_bit, source, NULL, NULL);
    if (cfg.v34 == NULL)
        return NULL;
    if (getenv("EICON_V90A_PHASE3_V34_LOG")
            && atoi(getenv("EICON_V90A_PHASE3_V34_LOG")) != 0) {
        logging_state_t *log = v34_get_logging_state(cfg.v34);

        if (log != NULL)
            span_log_set_level(log, SPAN_LOG_SHOW_SEVERITY
                                    | SPAN_LOG_SHOW_PROTOCOL | SPAN_LOG_FLOW);
    }
    fprintf(stderr, "[phase3-stream] DIL preset=%s n=%u h0=%u lsp=%u ltp=%u\n",
            v90_dil_preset_name(preset), dil.n, dil.h[0], dil.lsp, dil.ltp);
    *v34_out = cfg.v34;
    v90_analogue_phase3_t *phase3 = v90_analogue_phase3_init(&cfg);

    if (phase3 == NULL) {
        v34_free(*v34_out);
        *v34_out = NULL;
    }
    return phase3;
}

static int stream_mode(const char *reset_path, bool data_stream)
{
    const char *start_text = getenv("EICON_V90A_PHASE3_START_S");
    const char *gain_text = getenv("EICON_V90A_PHASE3_TX_GAIN");
    double tx_gain = gain_text && *gain_text ? strtod(gain_text, NULL) : 1.0;
    long start_frames = 0;
    time_t reset_mtime = 0;
    v90_analogue_phase3_t *phase3 = NULL;
    v34_state_t *v34 = NULL;
    idle_bit_source_t idle_source = {0};
    idle_bit_source_t side_source = {0};
    uint8_t downstream[160], upstream[160];
    uint8_t input_bits[DATA_BYTES], output_bits[DATA_BYTES];
    int16_t linear[160];
    v90_analogue_tx_stage_t last_tx = V90A_TX_INITIAL_SILENCE;
    v90_analogue_rx_stage_t last_rx = V90A_RX_HUNT_SD;
    unsigned last_events = 0;
    bool cp_logged = false;
    bool data_connected = false;
    bool data_carrier_switched = false;
    bool sideband = getenv("EICON_V90A_DATA_SIDEBAND")
        && atoi(getenv("EICON_V90A_DATA_SIDEBAND")) != 0;
    unsigned side_sequence = 0;
    bool data_high_carrier = getenv("EICON_V90A_PHASE3_DATA_HIGH_CARRIER")
        && atoi(getenv("EICON_V90A_PHASE3_DATA_HIGH_CARRIER")) != 0;

    if (start_text) {
        double seconds = strtod(start_text, NULL);
        if (seconds > 0.0)
            start_frames = (long)(seconds * 50.0);
    }
    fprintf(stderr, "[phase3-stream] start frame=%ld\n", start_frames);
    fprintf(stderr, "[phase3-stream] tx gain=%.6g\n", tx_gain);
    for (;;) {
        size_t got = fread(downstream, 1, sizeof(downstream), stdin);
        int produced;

        if (got == 0)
            break;
        if (got != sizeof(downstream)) {
            fprintf(stderr, "phase-3 stream received a short frame\n");
            v90_analogue_phase3_free(phase3);
            return 1;
        }
        if (data_stream) {
            uint8_t header[4];
            unsigned input_count;

            if (fread(header, 1, sizeof(header), stdin) != sizeof(header)
                    || fread(input_bits, 1, sizeof(input_bits), stdin)
                           != sizeof(input_bits)) {
                fprintf(stderr, "phase-3 data stream received a short request\n");
                v90_analogue_phase3_free(phase3);
                return 1;
            }
            input_count = (unsigned)header[0] | ((unsigned)header[1] << 8);
            queue_input_bits(&idle_source, input_bits, input_count);
            if (sideband)
                queue_input_bits(&side_source, input_bits, input_count);
            idle_source.consumed_frame = 0;
        }
        if (reset_path) {
            struct stat reset_stat;
            if (stat(reset_path, &reset_stat) == 0
                    && reset_stat.st_mtime > reset_mtime) {
                v90_analogue_phase3_free(phase3);
                v34_free(v34);
                v34 = NULL;
                phase3 = new_phase3(&v34, &idle_source);
                if (!phase3) {
                    fprintf(stderr, "phase-3 reset initialization failed\n");
                    return 1;
                }
                reset_mtime = reset_stat.st_mtime;
                start_frames = 0;
                cp_logged = false;
                data_connected = false;
                memset(&side_source, 0, sizeof(side_source));
                side_sequence = 0;
                fprintf(stderr, "[phase3-stream] reset initialized\n");
            }
        }
        if (start_frames > 0) {
            start_frames--;
            memset(upstream, 0xff, sizeof(upstream));
            if (fwrite(upstream, 1, sizeof(upstream), stdout) != sizeof(upstream))
                break;
            if (data_stream) {
                uint8_t header[4] = {0};
                memset(output_bits, 0, sizeof(output_bits));
                if (fwrite(header, 1, sizeof(header), stdout) != sizeof(header)
                        || fwrite(output_bits, 1, sizeof(output_bits), stdout)
                               != sizeof(output_bits))
                    break;
            }
            fflush(stdout);
            continue;
        }
        if (!phase3) {
            phase3 = new_phase3(&v34, &idle_source);
            if (!phase3) {
                fprintf(stderr, "phase-3 streaming initialization failed\n");
                return 1;
            }
            fprintf(stderr, "[phase3-stream] initialized\n");
        }
        unsigned events = v90_analogue_phase3_rx(phase3, downstream, (int)got);

        if ((events & V90A4_RX_EVENT_DATA) != 0) {
            data_connected = true;
            fprintf(stderr,
                    "[phase3-stream] CONNECTED data-ready idle-bits=%llu "
                    "upstream-rate=%d\n",
                    (unsigned long long)idle_source.bits,
                    v90_analogue_phase3_upstream_rate(phase3));
        }

        if (events != 0 && events != last_events) {
            fprintf(stderr, "[phase3-stream] rx-events=0x%08x\n", events);
            last_events = events;
        }
        if (data_high_carrier && !data_carrier_switched
                && v90_analogue_phase3_tx_stage(phase3)
                       == V90A_TX_B1_PENDING) {
            if (v34_tx_start_external_symbols(v34, 4, 1,
                                              get_zero_symbol, NULL) != 0)
                break;
            data_carrier_switched = true;
            fprintf(stderr,
                    "[phase3-stream] switched upstream data carrier high at B1 seam\n");
        }
        produced = v90_analogue_phase3_tx(phase3, linear, (int)got);
        if (produced != (int)sizeof(upstream)) {
            fprintf(stderr, "phase-3 stream produced %d samples\n", produced);
            v90_analogue_phase3_free(phase3);
            return 1;
        }
        for (int i = 0; i < produced; ++i)
            upstream[i] = pcmu_encode((int)lrint((double)linear[i] * tx_gain));
        if (sideband && data_connected)
            embed_sideband(upstream, &side_source, &side_sequence);
        if (v90_analogue_phase3_tx_stage(phase3) != last_tx
                || v90_analogue_phase3_rx_stage(phase3) != last_rx) {
            last_tx = v90_analogue_phase3_tx_stage(phase3);
            last_rx = v90_analogue_phase3_rx_stage(phase3);
            fprintf(stderr, "[phase3-stream] tx=%s rx=%d\n",
                    tx_name(last_tx), (int)last_rx);
        }
        if (!cp_logged && v90_analogue_phase3_phase4_state(phase3) != NULL) {
            const vpcm_cp_frame_t *cpt = v90_analogue_phase3_cpt(phase3);
            const vpcm_cp_frame_t *cp = v90_analogue_phase3_cp(phase3);

            if (cpt != NULL && cp != NULL) {
                fprintf(stderr,
                        "[phase3-stream] cp-config cpt(drn=%u k=%d sr=%u ld=%u n=%u up=0x%04x gain=%u) cp(drn=%u k=%d sr=%u ld=%u n=%u up=0x%04x gain=%u)\n",
                        cpt->drn, vpcm_cp_drn_to_k_sr(cpt->drn, cpt->shaping_redundancy),
                        cpt->shaping_redundancy, cpt->shaping_lookahead,
                        cpt->constellation_count, cpt->upstream_rate_mask,
                        cpt->trn1d_gain_q3_13,
                        cp->drn, vpcm_cp_drn_to_k_sr(cp->drn, cp->shaping_redundancy),
                        cp->shaping_redundancy, cp->shaping_lookahead,
                        cp->constellation_count, cp->upstream_rate_mask,
                        cp->trn1d_gain_q3_13);
                cp_logged = true;
            }
        }
        if (fwrite(upstream, 1, sizeof(upstream), stdout) != sizeof(upstream))
            break;
        if (data_stream) {
            unsigned received = pack_output_bits(phase3, output_bits);
            if (data_connected)
                received |= 0x8000U;
            unsigned consumed = idle_source.consumed_frame;
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
    if (phase3 != NULL) {
        if (sideband)
            fprintf(stderr,
                    "[phase3-stream] upstream sideband frames=%u queued=%u\n",
                    side_sequence, side_source.count);
        const v90_analogue_phase4_t *p4 =
            v90_analogue_phase3_phase4_state(phase3);

        if (p4 != NULL) {
            const v90_analogue_mp_t *mp = v90_analogue_phase4_mp(p4);

            fprintf(stderr,
                    "[phase3-stream] p4-final stage=%s R=%d TRN2d=%d MP=%d ones=%d failures=%d out=%d overflow=%d B1d=%d B1err=%d\n",
                    v90_analogue_phase4_stage_name(v90_analogue_phase4_stage(p4)),
                    v90_analogue_phase4_r_symbols(p4),
                    v90_analogue_phase4_trn2d_symbols(p4),
                    v90_analogue_phase4_mp_frames(p4),
                    v90_analogue_phase4_trn2d_ones(p4),
                    v90_analogue_phase4_demap_failures(p4),
                    v90_analogue_phase4_demap_out_of_constellation(p4),
                    v90_analogue_phase4_demap_modulus_overflow(p4),
                    v90_analogue_phase4_b1d_frames(p4),
                    v90_analogue_phase4_b1d_bit_errors(p4));
            if (mp != NULL)
                fprintf(stderr,
                        "[phase3-stream] mp max-dfn=%u trellis=%u nonlinear=%u expanded=%u ack=%u rate-mask=0x%04x precoder=%d,%d,%d,%d,%d,%d\n",
                        (unsigned)mp->max_drn, (unsigned)mp->trellis,
                        mp->nonlinear ? 1U : 0U,
                        mp->expanded_shaping ? 1U : 0U,
                        mp->acknowledge ? 1U : 0U,
                        (unsigned)mp->rate_mask,
                        mp->precoder[0][0], mp->precoder[0][1],
                        mp->precoder[1][0], mp->precoder[1][1],
                        mp->precoder[2][0], mp->precoder[2][1]);
        }
        fprintf(stderr, "[phase3-stream] tx-data-bits=%llu\n",
                (unsigned long long)idle_source.bits);
    }
    v90_analogue_phase3_free(phase3);
    v34_free(v34);
    return 0;
}

int main(int argc, char **argv)
{
    FILE *input, *output = NULL;
    long start = 0, end, size;
    uint8_t *downstream;
    v90_dil_preset_t preset = V90_DIL_PRESET_DEFAULT_JA;
    v90_dil_desc_t dil;
    v90_analogue_phase3_config_t cfg;
    v90_analogue_phase3_t *phase3;
    v34_state_t *v34 = NULL;
    idle_bit_source_t idle_source = {0};
    v90_analogue_tx_stage_t last_tx;
    v90_analogue_rx_stage_t last_rx;

    if (argc >= 2 && argc <= 5 && strcmp(argv[1], "--stream") == 0) {
        const char *reset_path = NULL;
        bool data_stream = false;
        for (int i = 2; i < argc; i++) {
            if (!strcmp(argv[i], "--reset-file") && i + 1 < argc)
                reset_path = argv[++i];
            else if (!strcmp(argv[i], "--data-stream"))
                data_stream = true;
            else
                return 2;
        }
        return stream_mode(reset_path, data_stream);
    }
    if (argc < 2 || argc > 3) {
        fprintf(stderr, "usage: %s downstream.ulaw [upstream.ulaw]\n", argv[0]);
        return 2;
    }
    input = fopen(argv[1], "rb");
    if (!input) {
        fprintf(stderr, "%s: %s\n", argv[1], strerror(errno));
        return 2;
    }
    fseek(input, 0, SEEK_END);
    size = ftell(input);
    fseek(input, 0, SEEK_SET);
    if (size <= 0 || size > 2000000) {
        fprintf(stderr, "invalid capture size\n");
        fclose(input);
        return 2;
    }
    downstream = malloc((size_t)size);
    if (!downstream || fread(downstream, 1, (size_t)size, input) != (size_t)size) {
        fprintf(stderr, "short read or allocation failure\n");
        free(downstream);
        fclose(input);
        return 2;
    }
    fclose(input);
    end = size;
    if (argc == 3) {
        output = fopen(argv[2], "wb");
        if (!output) {
            fprintf(stderr, "%s: %s\n", argv[2], strerror(errno));
            free(downstream);
            return 2;
        }
    }
    memset(&dil, 0, sizeof(dil));
    if (!v90_dil_preset_load(preset, &dil)) {
        fprintf(stderr, "could not load default DIL preset\n");
        free(downstream);
        if (output) fclose(output);
        return 1;
    }
    memset(&cfg, 0, sizeof(cfg));
    cfg.law = V90_LAW_ULAW;
    cfg.baud_rate_code = 4;
    cfg.high_carrier = false;
    cfg.u_info = 78;
    cfg.dil = dil;
    cfg.dil_coverage = 1.0;
    v34 = v34_init(NULL, 3200, 28800, true, true,
                   get_idle_bit, &idle_source, NULL, NULL);
    cfg.v34 = v34;
    if (!v34) {
        fprintf(stderr, "V.34 transmitter initialization failed\n");
        free(downstream);
        if (output) fclose(output);
        return 1;
    }
    phase3 = v90_analogue_phase3_init(&cfg);
    if (!phase3) {
        fprintf(stderr, "phase-3 initialization failed\n");
        v34_free(v34);
        free(downstream);
        if (output) fclose(output);
        return 1;
    }
    last_tx = v90_analogue_phase3_tx_stage(phase3);
    last_rx = v90_analogue_phase3_rx_stage(phase3);
    for (long i = start; i < end; i += 160) {
        int count = (int)((end - i) > 160 ? 160 : (end - i));
        int16_t upstream[160];
        unsigned events;
        int produced;

        events = v90_analogue_phase3_rx(phase3, downstream + i, count);
        produced = v90_analogue_phase3_tx(phase3, upstream, count);
        if (output)
            for (int j = 0; j < produced; ++j)
                fputc(pcmu_encode(upstream[j]), output);
        if (v90_analogue_phase3_tx_stage(phase3) != last_tx
                || v90_analogue_phase3_rx_stage(phase3) != last_rx
                || events) {
            last_tx = v90_analogue_phase3_tx_stage(phase3);
            last_rx = v90_analogue_phase3_rx_stage(phase3);
            printf("sample=%ld events=0x%x tx=%s rx=%d complete=%d data=%d\n",
                   i, events, tx_name(last_tx), last_rx,
                   v90_analogue_phase3_complete(phase3),
                   v90_analogue_phase3_data_ready(phase3));
        }
    }
    printf("final tx=%s rx=%d complete=%d data=%d retrain=%d\n",
           tx_name(v90_analogue_phase3_tx_stage(phase3)),
           v90_analogue_phase3_rx_stage(phase3),
           v90_analogue_phase3_complete(phase3),
           v90_analogue_phase3_data_ready(phase3),
           v90_analogue_phase3_retrain_due(phase3));
    printf("tx-data-bits=%llu\n", (unsigned long long)idle_source.bits);
    v90_analogue_phase3_free(phase3);
    v34_free(v34);
    free(downstream);
    if (output) fclose(output);
    return 0;
}
