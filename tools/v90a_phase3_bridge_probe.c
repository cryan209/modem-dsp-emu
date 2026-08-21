/*
 * Exercise the sibling's coupled V.90A Phase-3 state machine against a PCMU
 * downstream capture and optionally write the generated upstream PCMU.
 *
 * The probe is deliberately offline. It establishes whether the sibling
 * event/state translator can consume the native V90D waveform before it is
 * placed behind the Eicon media boundary.
 */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
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

    memset(&dil, 0, sizeof(dil));
    if (!v90_dil_preset_load(V90_DIL_PRESET_DEFAULT_JA, &dil))
        return NULL;
    memset(&cfg, 0, sizeof(cfg));
    cfg.law = V90_LAW_ULAW;
    cfg.baud_rate_code = 4;
    cfg.high_carrier = false;
    cfg.u_info = 78;
    cfg.dil = dil;
    cfg.dil_coverage = 1.0;
    return v90_analogue_phase3_init(&cfg);
}

static int stream_mode(void)
{
    v90_analogue_phase3_t *phase3 = new_phase3();
    uint8_t downstream[160], upstream[160];
    int16_t linear[160];

    if (!phase3) {
        fprintf(stderr, "phase-3 streaming initialization failed\n");
        return 1;
    }
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
        (void)v90_analogue_phase3_rx(phase3, downstream, (int)got);
        produced = v90_analogue_phase3_tx(phase3, linear, (int)got);
        if (produced != (int)sizeof(upstream)) {
            fprintf(stderr, "phase-3 stream produced %d samples\n", produced);
            v90_analogue_phase3_free(phase3);
            return 1;
        }
        for (int i = 0; i < produced; ++i)
            upstream[i] = pcmu_encode(linear[i]);
        if (fwrite(upstream, 1, sizeof(upstream), stdout) != sizeof(upstream))
            break;
        fflush(stdout);
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

    if (argc == 2 && strcmp(argv[1], "--stream") == 0)
        return stream_mode();
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
