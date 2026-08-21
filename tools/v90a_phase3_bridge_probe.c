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

static int16_t pcmu_decode(uint8_t code)
{
    uint8_t v = (uint8_t)~code;
    int magnitude = (((v & 0x0f) << 1) + 33) << ((v >> 4) & 7);
    magnitude -= 33;
    return (int16_t)((v & 0x80) ? -magnitude : magnitude);
}

static uint8_t pcmu_encode(int sample)
{
    int sign = sample < 0;
    int magnitude = sample < 0 ? -sample : sample;
    int exponent = 0;
    int mantissa;

    if (magnitude > 8159) magnitude = 8159;
    magnitude += 33;
    while (magnitude > (0x3f << (exponent + 1)) && exponent < 7)
        exponent++;
    mantissa = (magnitude >> (exponent + 1)) & 0x0f;
    return (uint8_t)~((sign << 7) | (exponent << 4) | mantissa);
}

static const char *tx_name(v90_analogue_tx_stage_t stage)
{
    return v90_analogue_tx_stage_name(stage);
}

static v90_analogue_phase3_t *new_phase3(void)
{
    v90_dil_desc_t dil;
    v90_analogue_phase3_config_t cfg;
    const char *profile = getenv("EICON_V90A_PHASE3_DIL_PRESET");
    v90_dil_preset_t preset = V90_DIL_PRESET_DEFAULT_JA;

    memset(&dil, 0, sizeof(dil));
    if (profile && (!strcmp(profile, "courier")
                    || !strcmp(profile, "card")))
        preset = V90_DIL_PRESET_COURIER_STYLE;
    else if (profile && !strcmp(profile, "measurement"))
        preset = V90_DIL_PRESET_MEASUREMENT;
    if (!v90_dil_preset_load(preset, &dil))
        return NULL;
    memset(&cfg, 0, sizeof(cfg));
    cfg.law = V90_LAW_ULAW;
    cfg.baud_rate_code = 4;
    cfg.high_carrier = false;
    cfg.u_info = 78;
    cfg.dil = dil;
    cfg.dil_coverage = 1.0;
    fprintf(stderr, "[phase3-stream] DIL preset=%s n=%u h0=%u lsp=%u ltp=%u\n",
            v90_dil_preset_name(preset), dil.n, dil.h[0], dil.lsp, dil.ltp);
    return v90_analogue_phase3_init(&cfg);
}

static int stream_mode(const char *reset_path)
{
    const char *start_text = getenv("EICON_V90A_PHASE3_START_S");
    const char *gain_text = getenv("EICON_V90A_PHASE3_TX_GAIN");
    double tx_gain = gain_text && *gain_text ? strtod(gain_text, NULL) : 1.0;
    long start_frames = 0;
    time_t reset_mtime = 0;
    v90_analogue_phase3_t *phase3 = NULL;
    uint8_t downstream[160], upstream[160];
    int16_t linear[160];
    v90_analogue_tx_stage_t last_tx = V90A_TX_INITIAL_SILENCE;
    v90_analogue_rx_stage_t last_rx = V90A_RX_HUNT_SD;
    unsigned last_events = 0;
    bool cp_logged = false;

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
        if (reset_path) {
            struct stat reset_stat;
            if (stat(reset_path, &reset_stat) == 0
                    && reset_stat.st_mtime > reset_mtime) {
                v90_analogue_phase3_free(phase3);
                phase3 = new_phase3();
                if (!phase3) {
                    fprintf(stderr, "phase-3 reset initialization failed\n");
                    return 1;
                }
                reset_mtime = reset_stat.st_mtime;
                start_frames = 0;
                cp_logged = false;
                fprintf(stderr, "[phase3-stream] reset initialized\n");
            }
        }
        if (start_frames > 0) {
            start_frames--;
            memset(upstream, 0xff, sizeof(upstream));
            if (fwrite(upstream, 1, sizeof(upstream), stdout) != sizeof(upstream))
                break;
            fflush(stdout);
            continue;
        }
        if (!phase3) {
            phase3 = new_phase3();
            if (!phase3) {
                fprintf(stderr, "phase-3 streaming initialization failed\n");
                return 1;
            }
            fprintf(stderr, "[phase3-stream] initialized\n");
        }
        unsigned events = v90_analogue_phase3_rx(phase3, downstream, (int)got);

        if (events != 0 && events != last_events) {
            fprintf(stderr, "[phase3-stream] rx-events=0x%08x\n", events);
            last_events = events;
        }
        produced = v90_analogue_phase3_tx(phase3, linear, (int)got);
        if (produced != (int)sizeof(upstream)) {
            fprintf(stderr, "phase-3 stream produced %d samples\n", produced);
            v90_analogue_phase3_free(phase3);
            return 1;
        }
        for (int i = 0; i < produced; ++i)
            upstream[i] = pcmu_encode((int)lrint((double)linear[i] * tx_gain));
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
        fflush(stdout);
    }
    if (phase3 != NULL) {
        const v90_analogue_phase4_t *p4 =
            v90_analogue_phase3_phase4_state(phase3);

        if (p4 != NULL) {
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
        }
    }
    v90_analogue_phase3_free(phase3);
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
    v90_analogue_tx_stage_t last_tx;
    v90_analogue_rx_stage_t last_rx;

    if (argc >= 2 && argc <= 4 && strcmp(argv[1], "--stream") == 0) {
        const char *reset_path = NULL;
        if (argc == 4 && strcmp(argv[2], "--reset-file") == 0)
            reset_path = argv[3];
        else if (argc != 2)
            return 2;
        return stream_mode(reset_path);
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
    phase3 = v90_analogue_phase3_init(&cfg);
    if (!phase3) {
        fprintf(stderr, "phase-3 initialization failed\n");
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
    v90_analogue_phase3_free(phase3);
    free(downstream);
    if (output) fclose(output);
    return 0;
}
