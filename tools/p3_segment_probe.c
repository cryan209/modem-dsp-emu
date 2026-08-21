/*
 * Classify a PCMU capture with the sibling streaming Phase-3 demodulator.
 *
 * Build on macOS from this repository with:
 *   cc -O2 -I/Users/scottcryan/v90modem \
 *      tools/p3_segment_probe.c /Users/scottcryan/v90modem/p3_demod.o \
 *      -lm -o /tmp/p3_segment_probe
 *
 * This is an analysis tool only. It does not participate in the modem path.
 */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "/Users/scottcryan/v90modem/p3_demod.h"

static int16_t pcmu(uint8_t code)
{
    uint8_t v = (uint8_t)~code;
    int magnitude = (((v & 0x0f) << 1) + 33) << ((v >> 4) & 7);
    magnitude -= 33;
    return (int16_t)((v & 0x80) ? -magnitude : magnitude);
}

static const char *signal_name(p3_signal_type_t type)
{
    switch (type) {
    case P3_SIGNAL_S: return "S";
    case P3_SIGNAL_S_BAR: return "S-bar";
    case P3_SIGNAL_PP: return "PP";
    case P3_SIGNAL_TRN: return "TRN";
    case P3_SIGNAL_J: return "J";
    case P3_SIGNAL_J_PRIME: return "J-prime";
    case P3_SIGNAL_RU: return "Ru";
    case P3_SIGNAL_UR: return "uR";
    case P3_SIGNAL_SILENCE: return "silence";
    default: return "unknown";
    }
}

int main(int argc, char **argv)
{
    FILE *file;
    long from = 0, to = -1, size;
    uint8_t *encoded;
    int16_t *samples;
    p3_result_t *result;

    if (argc < 2 || argc > 4) {
        fprintf(stderr, "usage: %s capture.ulaw [from_seconds] [to_seconds]\n",
                argv[0]);
        return 2;
    }
    file = fopen(argv[1], "rb");
    if (!file) {
        fprintf(stderr, "%s: %s\n", argv[1], strerror(errno));
        return 2;
    }
    fseek(file, 0, SEEK_END);
    size = ftell(file);
    fseek(file, 0, SEEK_SET);
    if (argc >= 3) from = strtol(argv[2], NULL, 10) * 8000L;
    if (argc == 4) to = strtol(argv[3], NULL, 10) * 8000L;
    if (to < 0 || to > size) to = size;
    if (from < 0 || from >= to) {
        fprintf(stderr, "invalid sample window\n");
        fclose(file);
        return 2;
    }
    encoded = malloc((size_t)(to - from));
    samples = malloc((size_t)(to - from) * sizeof(*samples));
    if (!encoded || !samples) {
        fprintf(stderr, "allocation failed\n");
        fclose(file);
        free(encoded);
        free(samples);
        return 2;
    }
    fseek(file, from, SEEK_SET);
    if (fread(encoded, 1, (size_t)(to - from), file) != (size_t)(to - from)) {
        fprintf(stderr, "short read\n");
        fclose(file);
        free(encoded);
        free(samples);
        return 2;
    }
    fclose(file);
    for (long i = 0; i < to - from; ++i) samples[i] = pcmu(encoded[i]);
    free(encoded);

    /* Native V90 traces most often use the low 3200-baud carrier. Try both
     * carrier selections explicitly; the better segment set is informative. */
    result = p3_demod_run(samples, (int)(to - from), (int)from,
                           P3_BAUD_3200, P3_CARRIER_LOW, 8000);
    if (!result) {
        fprintf(stderr, "demodulation failed\n");
        free(samples);
        return 1;
    }
    printf("symbols=%d segments=%d locked=%d snr=%.2f carrier=%.1f baud=%.1f\n",
           result->symbol_count, result->segment_count, result->locked,
           result->snr_estimate_db, result->carrier_freq_estimate,
           result->baud_rate_estimate);
    for (int i = 0; i < result->segment_count; ++i) {
        const p3_segment_t *segment = &result->segments[i];
        if (segment->type == P3_SIGNAL_UNKNOWN
                || segment->type == P3_SIGNAL_SILENCE)
            continue;
        printf("segment %d type=%s start=%d length=%d confidence=%.3f\n",
               i, signal_name(segment->type), segment->start_sample,
               segment->length, segment->confidence);
    }
    p3_result_free(result);
    free(samples);
    return 0;
}
