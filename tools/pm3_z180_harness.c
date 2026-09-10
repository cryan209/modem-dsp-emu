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

#define PHYS_SIZE (1u << 20)

typedef struct {
    uint8_t memory[PHYS_SIZE];
    uint64_t reads, writes, inputs, outputs;
    uint64_t coverage[1u << 16];
    uint8_t asic_index;
    uint8_t port_fe;
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
    if (m->trace_io)
        printf("OUT %04x=%02x pc=%04x\n", (unsigned)port, value,
               (unsigned)cpu->old_pc);
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

int main(int argc, char **argv) {
    unsigned long steps = 10000000;
    unsigned long timer_every = 0;
    unsigned long extint_every = 0;
    unsigned extint = 0, extint_data = 0;
    unsigned port_fe = 0;
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
        else if (argv[i][0] != '-') image = argv[i];
        else {
            fprintf(stderr, "usage: %s [--trace-io] [--steps N] "
                    "[--timer-every N] m2d.bin\n", argv[0]);
            return 2;
        }
    }
    if (!image) {
        fprintf(stderr, "usage: %s [--trace-io] [--steps N] "
                "[--timer-every N] m2d.bin\n", argv[0]);
        return 2;
    }

    pm3_t *m = calloc(1, sizeof(*m));
    if (!m) return 2;
    memset(m->memory, 0xff, sizeof(m->memory));
    m->trace_io = trace_io;
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
