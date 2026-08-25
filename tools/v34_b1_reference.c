/* Emit the reset-state V.34 B1 mapping frame as little-endian Q9.7 pairs. */
#include "spandsp.h"
#include "spandsp/private/bitstream.h"
#include "spandsp/private/logging.h"
#include "spandsp/private/modem_echo.h"
#include "spandsp/private/power_meter.h"
#include "spandsp/private/v34.h"
#include "v34_tables.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static int get_one(void *user_data)
{
    (void)user_data;
    return 1;
}

static void get_zero_symbol(void *user_data, float *re, float *im)
{
    (void)user_data;
    *re = 0.0f;
    *im = 0.0f;
}

int main(int argc, char **argv)
{
    int bit_rate = (argc > 2) ? atoi(argv[2]) : 31200;
    int bit_rate_n = bit_rate/2400;
    v34_state_t *tx = v34_init(NULL, 3200, bit_rate, true, true,
                               get_one, NULL, NULL, NULL);
    int16_t frame[16];

    if (argc > 1) {
        int16_t amp[160];

        if (tx == NULL
                || v34_tx_start_external_symbols(tx, V34_BAUD_RATE_3200, 0,
                                                  get_zero_symbol, NULL) != 0)
            return 1;
        for (int block = 0; block < 500; block++) {
            int len;

            if (block == 50
                    && v34_v90_begin_tx_data(tx, bit_rate_n, V34_TRELLIS_16,
                                             0, 0, NULL) != 0)
                return 1;
            len = v34_tx(tx, amp, 160);

            if (len != 160)
                return 1;
            for (int i = 0; i < len; i++) {
                uint8_t code = linear_to_ulaw(amp[i]);

                if (fwrite(&code, 1, 1, stdout) != 1)
                    return 1;
            }
        }
        v34_free(tx);
        return 0;
    }
    if (tx == NULL || v34_seed_tx_data(tx, bit_rate_n, V34_TRELLIS_16,
                                       0, 0, NULL) != 0)
        return 1;
    tx->tx.scrambler_tap = 4;
    tx->tx.super_frame = tx->tx.parms.j - 1;
    tx->tx.v0_pattern = (uint16_t)(2*(tx->tx.parms.j - 1));
    for (int mapping = 0; mapping < tx->tx.parms.p; mapping++) {
        if (v34_get_mapping_frame_state(tx, frame) != 16
                || fwrite(frame, sizeof(frame[0]), 16, stdout) != 16)
            return 1;
    }
    v34_free(tx);
    return 0;
}
