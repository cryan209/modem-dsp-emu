/* Minimal PortMaster modem-controller host for BinaryMelodies/x80-emulator.
 *
 * This is deliberately a board probe, not a PM3 hardware model.  It boots the
 * Z180 image, records external I/O, and stops after a bounded instruction
 * count.  The x80 Z180 core remains a separately built LGPL dependency.
 */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpu.h"

extern void z180_reset(x80_state_t *);
extern bool z180_step(x80_state_t *, bool);
extern void z180_exception_intint(x80_state_t *, int);
extern void z180_exception_extint(x80_state_t *, int, int, void *);
extern address_t z180_get_address(x80_state_t *, address_t, x80_access_type_t);

#define PHYS_SIZE (1u << 20)

typedef struct {
    uint8_t memory[PHYS_SIZE];
    uint64_t reads, writes, inputs, outputs;
    uint64_t coverage[1u << 16];
    uint8_t asic_index;
    uint8_t port_fe;
    uint8_t rx_fifo[256], tx_fifo[256];
    unsigned rx_len, rx_pos, tx_len;
    bool trace_io;
} pm3_t;

static pm3_t *machine(x80_state_t *cpu) { return (pm3_t *)cpu->user_data; }

static uint8_t memory_read(x80_state_t *cpu, address_t address) {
    pm3_t *m = machine(cpu);
    m->reads++;
    return m->memory[address & (PHYS_SIZE - 1)];
}

static void memory_write(x80_state_t *cpu, address_t address, uint8_t value) {
    pm3_t *m = machine(cpu);
    m->writes++;
    m->memory[address & (PHYS_SIZE - 1)] = value;
}

static uint8_t port_read(x80_state_t *cpu, ioaddress_t port) {
    pm3_t *m = machine(cpu);
    m->inputs++;
    uint8_t value = 0;
    if ((port & 0xffff) == 0x00fe) {
        value = m->port_fe;
        m->port_fe = 0; /* edge/status acknowledgement for a single probe */
    }
    /* The receive ISR at 04ac loops while F5 bit 5 is clear.  The ASIC sets
     * it once the FIFO has become empty (the test happens after each read). */
    if ((port & 0xffff) == 0x00f5)
        value = m->rx_pos < m->rx_len ? 0 : 0x20;
    if ((port & 0xff) == 0xf0 && m->rx_pos < m->rx_len)
        value = m->rx_fifo[m->rx_pos++];
    if (m->trace_io)
        printf("IN  %04x -> %02x pc=%04x asic=%02x\n", (unsigned)port,
               value, (unsigned)cpu->old_pc, m->asic_index);
    return value;
}

static void port_write(x80_state_t *cpu, ioaddress_t port, uint8_t value) {
    pm3_t *m = machine(cpu);
    m->outputs++;
    if ((port & 0xffff) == 0x00c0 || (port & 0xffff) == 0x00c2)
        m->asic_index = value;
    if ((port & 0xff) == 0xf0 && m->tx_len < sizeof(m->tx_fifo))
        m->tx_fifo[m->tx_len++] = value;
    if (m->trace_io)
        printf("OUT %04x=%02x pc=%04x\n", (unsigned)port, value,
               (unsigned)cpu->old_pc);
}

static unsigned parse_hex_bytes(const char *s, uint8_t *out, unsigned capacity) {
    unsigned n = 0;
    while (*s) {
        char *end;
        unsigned long value = strtoul(s, &end, 16);
        if (end == s || value > 0xff || n == capacity) {
            fprintf(stderr, "invalid hex byte list: %s\n", s);
            exit(2);
        }
        out[n++] = (uint8_t)value;
        s = end;
        if (*s == ',' || *s == ':' || *s == ' ') s++;
        else if (*s) { fprintf(stderr, "invalid hex byte separator\n"); exit(2); }
    }
    return n;
}

static unsigned long number(const char *s) {
    char *end;
    unsigned long value = strtoul(s, &end, 0);
    if (!*s || *end) {
        fprintf(stderr, "invalid number: %s\n", s);
        exit(2);
    }
    return value;
}

static uint8_t logical_byte(pm3_t *m, x80_state_t *cpu, uint16_t address) {
    address_t physical = z180_get_address(cpu, address,
                                          X80_ACCESS_TYPE_READ);
    return m->memory[physical & (PHYS_SIZE - 1)];
}

static uint16_t logical_word(pm3_t *m, x80_state_t *cpu, uint16_t address) {
    return logical_byte(m, cpu, address) |
           ((uint16_t)logical_byte(m, cpu, address + 1) << 8);
}

int main(int argc, char **argv) {
    unsigned long steps = 10000000;
    unsigned long timer_every = 0;
    unsigned long extint_every = 0;
    unsigned long rx_at = 200000;
    unsigned extint = 0, extint_data = 0;
    unsigned port_fe = 0;
    const char *rx_hex = NULL;
    bool trace_io = false;
    const char *image = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--trace-io")) trace_io = true;
        else if (!strcmp(argv[i], "--steps") && i + 1 < argc)
            steps = number(argv[++i]);
        else if (!strcmp(argv[i], "--timer-every") && i + 1 < argc)
            timer_every = number(argv[++i]);
        else if (!strcmp(argv[i], "--extint-every") && i + 1 < argc)
            extint_every = number(argv[++i]);
        else if (!strcmp(argv[i], "--extint") && i + 1 < argc)
            extint = number(argv[++i]);
        else if (!strcmp(argv[i], "--extint-data") && i + 1 < argc)
            extint_data = number(argv[++i]);
        else if (!strcmp(argv[i], "--port-fe") && i + 1 < argc)
            port_fe = number(argv[++i]);
        else if (!strcmp(argv[i], "--rx-hex") && i + 1 < argc)
            rx_hex = argv[++i];
        else if (!strcmp(argv[i], "--rx-at") && i + 1 < argc)
            rx_at = number(argv[++i]);
        else if (argv[i][0] != '-') image = argv[i];
        else {
            fprintf(stderr, "usage: %s [--trace-io] [--steps N] "
                    "[--timer-every N] [--rx-hex BYTES] [--rx-at N] m2d.bin\n",
                    argv[0]);
            return 2;
        }
    }
    if (!image) {
        fprintf(stderr, "usage: %s [--trace-io] [--steps N] "
                "[--timer-every N] [--rx-hex BYTES] [--rx-at N] m2d.bin\n",
                argv[0]);
        return 2;
    }

    pm3_t *m = calloc(1, sizeof(*m));
    if (!m) return 2;
    memset(m->memory, 0xff, sizeof(m->memory));
    m->trace_io = trace_io;
    if (rx_hex) m->rx_len = parse_hex_bytes(rx_hex, m->rx_fifo,
                                             sizeof(m->rx_fifo));
    FILE *fp = fopen(image, "rb");
    if (!fp) { perror(image); return 2; }
    size_t size = fread(m->memory, 1, PHYS_SIZE, fp);
    fclose(fp);
    if (size < 255 * 1024 || size > 256 * 1024) {
        fprintf(stderr, "expected an approximately 256 KiB m2d image; got %zu bytes\n", size);
        return 2;
    }

    x80_state_t cpu = {0};
    cpu.cpu_type = X80_CPU_Z180;
    cpu.user_data = m;
    cpu.read_byte = memory_read;
    cpu.write_byte = memory_write;
    cpu.input_byte = port_read;
    cpu.output_byte = port_write;
    z180_reset(&cpu);
    for (unsigned long i = 0; i < steps; i++) {
        m->coverage[cpu.pc & 0xffff]++;
        if (!z180_step(&cpu, false)) {
            fprintf(stderr, "Z180 core stopped at step %lu\n", i);
            break;
        }
        if (timer_every && i && !(i % timer_every) && cpu.z180.ie1 &&
            (cpu.z180.ior[X80_Z180_IOR_TCR] & X80_Z180_TCR_TDE0))
            z180_exception_intint(&cpu, X80_Z180_INT_PRT0);
        if (extint_every && i && !(i % extint_every) && cpu.z180.ie1) {
            uint8_t data = extint_data;
            m->port_fe = port_fe;
            z180_exception_extint(&cpu, extint, 1, &data);
        }
        if (rx_hex && i >= rx_at && !(i % 10000) &&
            m->rx_pos < m->rx_len && cpu.z180.ie1) {
            uint8_t data = 0;
            m->port_fe = 0xa0; /* pending + receive-FIFO service */
            z180_exception_extint(&cpu, 0, 1, &data);
        }
    }

    unsigned distinct = 0;
    for (unsigned a = 0; a < 65536; a++) distinct += m->coverage[a] != 0;
    printf("pc=%04x sp=%04x af=%04x bc=%04x de=%04x hl=%04x\n",
           (unsigned)cpu.pc, (unsigned)cpu.sp, cpu.af, (unsigned)cpu.bc,
           (unsigned)cpu.de, (unsigned)cpu.hl);
    printf("I=%02x IL=%02x TCR=%02x IE=%u/%u\n", (unsigned)(cpu.ir >> 8),
           cpu.z180.ior[X80_Z180_IOR_IL], cpu.z180.ior[X80_Z180_IOR_TCR],
           cpu.z180.ie1, cpu.z180.ie2);
    printf("MMU CBR=%02x BBR=%02x CBAR=%02x; vectors 0244=%02x%02x "
           "0246=%02x%02x; tick=%02x%02x\n",
           cpu.z180.ior[X80_Z180_IOR_CBR], cpu.z180.ior[X80_Z180_IOR_BBR],
           cpu.z180.ior[X80_Z180_IOR_CBAR], m->memory[0x245], m->memory[0x244],
           m->memory[0x247], m->memory[0x246], m->memory[0x807c],
           m->memory[0x807b]);
    printf("executed=%u reads=%llu writes=%llu inputs=%llu outputs=%llu\n",
           distinct, (unsigned long long)m->reads, (unsigned long long)m->writes,
           (unsigned long long)m->inputs, (unsigned long long)m->outputs);
    if (rx_hex) {
        printf("fifo rx=%u/%u tx=%u", m->rx_pos, m->rx_len, m->tx_len);
        for (unsigned i = 0; i < m->tx_len; i++) printf(" %02x", m->tx_fifo[i]);
        putchar('\n');
        printf("rx-ring count=%u get=%04x put=%04x start=%04x end=%04x\n",
               logical_word(m, &cpu, 0x818e),
               logical_word(m, &cpu, 0xc865),
               logical_word(m, &cpu, 0xc867),
               logical_word(m, &cpu, 0xc869),
               logical_word(m, &cpu, 0xc86b));
    }
    for (int rank = 0; rank < 12; rank++) {
        uint64_t best = 0; unsigned address = 0;
        for (unsigned a = 0; a < 65536; a++)
            if (m->coverage[a] > best) { best = m->coverage[a]; address = a; }
        if (!best) break;
        printf("hot %04x %llu\n", address, (unsigned long long)best);
        m->coverage[address] = 0;
    }
    free(m);
    return 0;
}
