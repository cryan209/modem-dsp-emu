"""Driver-facing Diva 4BRI v2 board model with legacy and reentrant layouts.

Register offsets come from kernel/mi_pc.h and dsrv4bri.h; reset/IRQ and
adapter partitioning follow s_4bri.c and os_4bri.c in docs/divas4linux-master.
This is a board boundary, not a PCI bus or an ISDN signalling implementation.
Unsupported register accesses fail instead of pretending hardware exists.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path

MEMORY_SIZE = 0x1000000
INSTANCE_SIZE = MEMORY_SIZE // 4
SHARED_SIZE = 0x10000
RISC = 0x200
IRQ_TEST = 0x400
INTCSR = 0x69
IPACX_CONFIG = 0x300


class AdspIdma:
    """An owned ADSP core with the actual emulator's IDMA semantics."""
    def __init__(self):
        root = Path(__file__).resolve().parent / 'adsp2181emu'
        library = next((root / name for name in
                        ('libadsp2181.dylib', 'libadsp2181.so')
                        if (root / name).exists()), None)
        if library is None:
            raise RuntimeError('build tools/adsp2181emu first')
        self.lib = ctypes.CDLL(str(library))
        self.lib.adsp2181_create.restype = ctypes.c_void_p
        for name in ('destroy', 'reset', 'idma_addr_read', 'idma_data_read',
                     'idma_boot_held'):
            getattr(self.lib, 'adsp2181_' + name).argtypes = [ctypes.c_void_p]
        for name in ('idma_addr_write', 'idma_data_write'):
            getattr(self.lib, 'adsp2181_' + name).argtypes = [ctypes.c_void_p, ctypes.c_uint16]
        self.lib.adsp2181_set_idma_boot_hold.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.adsp2181_set_idma_boot_rearm.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.adsp2181_run.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.adsp2181_sport1_frame.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_int]
        self.lib.adsp2181_sport1_frame.restype = ctypes.c_uint32
        self.lib.adsp2181_fl0_falls.argtypes = [ctypes.c_void_p]
        self.lib.adsp2181_fl0_falls.restype = ctypes.c_uint64
        self.lib.adsp2181_pc.argtypes = [ctypes.c_void_p]
        self.lib.adsp2181_idle.argtypes = [ctypes.c_void_p]
        self.lib.adsp2181_dm.argtypes = [ctypes.c_void_p]
        self.lib.adsp2181_dm.restype = ctypes.POINTER(ctypes.c_uint16)
        self.lib.adsp2181_pm.argtypes = [ctypes.c_void_p]
        self.lib.adsp2181_pm.restype = ctypes.POINTER(ctypes.c_uint32)
        self.lib.adsp2181_idma_addr_read.restype = ctypes.c_uint16
        self.lib.adsp2181_idma_data_read.restype = ctypes.c_uint16
        previous = os.environ.get('EICON_ADSP_SPORT')
        os.environ['EICON_ADSP_SPORT'] = '1'
        try:
            self.cpu = self.lib.adsp2181_create()
        finally:
            if previous is None:
                os.environ.pop('EICON_ADSP_SPORT', None)
            else:
                os.environ['EICON_ADSP_SPORT'] = previous
        if not self.cpu:
            raise MemoryError('ADSP allocation failed')
        self.reset()

    def reset(self):
        previous = os.environ.get('EICON_ADSP_SPORT')
        os.environ['EICON_ADSP_SPORT'] = '1'
        try:
            self.lib.adsp2181_reset(self.cpu)
            self.lib.adsp2181_set_idma_boot_hold(self.cpu, 1)
            self.lib.adsp2181_set_idma_boot_rearm(self.cpu, 0)
        finally:
            if previous is None:
                os.environ.pop('EICON_ADSP_SPORT', None)
            else:
                os.environ['EICON_ADSP_SPORT'] = previous

    def read(self, address_port):
        name = 'idma_addr_read' if address_port else 'idma_data_read'
        return getattr(self.lib, 'adsp2181_' + name)(self.cpu)

    def write(self, address_port, value):
        name = 'idma_addr_write' if address_port else 'idma_data_write'
        getattr(self.lib, 'adsp2181_' + name)(self.cpu, value)

    def run(self, cycles):
        if self.lib.adsp2181_idma_boot_held(self.cpu):
            return 0
        return self.lib.adsp2181_run(self.cpu, cycles)

    def snapshot(self):
        dm, pm = self.lib.adsp2181_dm(self.cpu), self.lib.adsp2181_pm(self.cpu)
        return dict(fl0_falls=self.lib.adsp2181_fl0_falls(self.cpu), pc=self.lib.adsp2181_pc(self.cpu), idle=bool(self.lib.adsp2181_idle(self.cpu)),
                    held=bool(self.lib.adsp2181_idma_boot_held(self.cpu)),
                    descriptors=[dm[i] for i in range(0x2f00, 0x2f10)],
                    phase=dm[0x2df3], foreground=pm[0x2b0],
                    first_foreground=pm[0x29e], isr=pm[0x72],
                    sport_alignment=[dm[i] for i in range(0x2de6, 0x2dea)],
                    first_isr=pm[0x58],
                    sport_registers=[dm[i] for i in range(0x3ff0, 0x4000)],
                    rings={str(dm[i]): [dm[dm[i]+j] for j in range(10)]
                           for i in (0x2f09, 0x2f0a) if 0 < dm[i] < 0x3ff0},
                    first_channel={hex(base): [dm[base+j] for j in range(14)]
                                   for base in (0x2ea0, 0x2eae)})

    def dump(self, directory):
        """Save side-effect-free DM and packed 24-bit PM for the disassembler."""
        import struct
        if not self.cpu:
            raise RuntimeError('DSP is closed')
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        dm, pm = self.lib.adsp2181_dm(self.cpu), self.lib.adsp2181_pm(self.cpu)
        (directory / 'dm.bin').write_bytes(struct.pack('<16384H', *dm[:0x4000]))
        (directory / 'pm.bin').write_bytes(b''.join(
            (value & 0xffffff).to_bytes(3, 'little') for value in pm[:0x4000]))

    def sport1(self, word, cycles):
        if self.lib.adsp2181_idma_boot_held(self.cpu):
            return None
        return self.lib.adsp2181_sport1_frame(self.cpu, word, cycles)

    def close(self):
        if self.cpu:
            self.lib.adsp2181_destroy(self.cpu)
            self.cpu = None


class BriCard:
    """Four two-bearer controllers sharing SDRAM, board controls and IRQ.

    BAR0 exposes the supported PLX interrupt byte, BAR2 the 16 MiB SDRAM,
    and BAR3 the board controls and eight independent ADSP IDMA ports.
    BriMachine shares ``ram`` and drives device interrupts.
    Starting the CPU never fabricates a firmware-ready signature.
    """
    def __init__(self, dsp_factory=AdspIdma, reentrant=False, ram=None):
        from eicon_bri_pci import BriPci
        self.pci = BriPci()
        from eicon_bri_fpga import BriFpgaSerial
        self.fpga = BriFpgaSerial()
        if ram is None:
            ram = bytearray(MEMORY_SIZE)
        with memoryview(ram) as view:
            if view.readonly or not view.c_contiguous or view.itemsize != 1 or view.nbytes != MEMORY_SIZE:
                raise ValueError('SDRAM must be a writable contiguous 16 MiB byte buffer')
        # An external mapping is caller-owned. QEMU and MIPS can then share
        # the actual SDRAM bytes without an RPC for every memcpy access.
        self.ram = ram
        self.reentrant = reentrant
        self.risc = 0
        self.device_reset = 0
        self.ipacx_config = 0
        self.interrupt_enable = 0
        self.interrupt_pending = False
        self.pci.power_control = self._power_control
        self.bridge_control = 0
        self.local_mapping = True
        self._dsp_factory = dsp_factory
        self.dsps = {}
        self._fl0_seen = {}
        from eicon_ipacx import Ipacx
        self.ipacs = [Ipacx() for _ in range(4)]
        self.sport_peers = {}
        self.sport_latency_words = {}
        self._sport_delays = {}
        self.sport_input = [0xffff] * 8
        self.sport_output = [None] * 8
        self.sport_frames = [0] * 8
        self.closed = False
        self.cpu_control = None

    @property
    def cpu_running(self):
        return self.risc & 3 == 3

    @property
    def irq_asserted(self):
        return (self.pci.power_state == 0 and self.interrupt_pending
                and self.interrupt_enable & 9 == 9)

    def _power_control(self, previous, state):
        # The stopped spare clears its board test latch across D3hot/D0.
        # No BAR access was made during D3, so apply the observed result on resume.
        if previous == 3 and state == 0:
            self.interrupt_pending = False

    def raise_interrupt(self):
        self._check_open()
        self.interrupt_pending = True

    def _check_open(self):
        if self.closed:
            raise RuntimeError('card is closed')

    def controller_memory(self, controller, shared=False):
        self._check_open()
        if not 0 <= controller < 4:
            raise ValueError('controller must be 0..3')
        start = controller * INSTANCE_SIZE
        end = start + INSTANCE_SIZE
        if self.reentrant:
            start = controller * 0x11000 + (0x1000 if shared else 0)
            end = (controller + 1) * 0x11000
        return memoryview(self.ram)[(end - SHARED_SIZE if shared else start):end]

    def stage_protocol(self, controller, image):
        """Stage an unmodified, correctly linked legacy .2qN protocol image.

        No PRI anchor relocation, DSP table synthesis or ready signature is
        applied. The caller may then fill configuration and DSP-code tables
        through the same SDRAM windows before releasing a MIPS runner.
        """
        from eicon_mips_image import derive_layout
        if self.reentrant:
            raise RuntimeError('use load_reentrant for a reentrant board')
        window = self.controller_memory(controller)
        if self.cpu_running:
            raise RuntimeError('halt the board CPU before staging firmware')
        image = Path(image)
        layout = derive_layout(image)
        expected = 0x80000000 + controller * INSTANCE_SIZE
        if layout.base != expected:
            raise ValueError(f'controller {controller} requires image linked at '
                             f'{expected:#x}, got {layout.base:#x}')
        if layout.size > INSTANCE_SIZE - SHARED_SIZE:
            raise ValueError('protocol image overlaps shared RAM')
        window[:layout.size] = image.read_bytes()
        return layout

    def load_reentrant(self, image, combifile, card_type=53):
        """Apply the shipping reentrant loader ABI, including DSP tables."""
        import struct
        from eicon_mips_image import derive_layout
        from eicon_dsp_stage import build_dsp_code_image
        self._check_open()
        if self.cpu_running or not self.reentrant:
            raise RuntimeError('reentrant loading requires a halted reentrant board')
        layout = derive_layout(Path(image))
        base = (layout.protocol_end + 15) & ~15
        staged = build_dsp_code_image(Path(combifile), card_type, base)
        end = staged.end_addr & 0x1FFFFFFF
        if layout.base != 0x80000000 or layout.size > (base & 0x1FFFFFFF) or end > MEMORY_SIZE:
            raise ValueError('protocol/DSP image does not fit board SDRAM')
        self.ram[:layout.size] = Path(image).read_bytes()
        self.ram[0x68:0x6a] = bytes((4, card_type))
        struct.pack_into('<I', self.ram, 0x6c, base)
        offset = base & 0x1FFFFFFF
        self.ram[offset:end] = staged.data
        for i in range(4):
            shared = self.controller_memory(i, True)
            shared[:256] = bytes(256)
            # divaload.c: DMLT protocol selection is id | 0x80; ETSI id=1.
            shared[19] = 0x81
            shared[224:231] = bytes((0x34, 4)) + struct.pack('<I', len(staged.data)) + b'\0'
        return layout, staged

    def configure_port(self, controller, role='te', law='pcmu', stable_l2=2):
        """Shipping fixed PCINIT fields; call after staging and before boot."""
        if self.cpu_running:
            raise RuntimeError('halt CPU before configuring a port')
        if role not in ('te', 'nt') or law not in ('pcma', 'pcmu') or stable_l2 not in (0, 1, 2):
            raise ValueError('invalid BRI port configuration')
        shared = self.controller_memory(controller, True)
        shared[8] = 1  # cfg_get_tei: (fixed TEI 0 << 1) | 1
        shared[9] = 1  # NT2 / point-to-point
        shared[14] = stable_l2 | (8 if role == 'nt' else 0)
        shared[16] = 1 if law == 'pcma' else 2
        shared[19] = 0x81  # DMLT ETSI, as divaload configures it
        shared[22] = 0x80
        shared[26] = 53

    def snapshot(self):
        self._check_open()
        return dict(model='diva-4bri-v2', cpu_running=self.cpu_running, fpga=self.fpga.snapshot(),
                    local_mapping=self.local_mapping, bridge_control=self.bridge_control,
                    irq_asserted=bool(self.irq_asserted),
                    interrupt_pending=self.interrupt_pending,
                    allocated_dsps=sorted(self.dsps),
                    dsp_state={str(i): core.snapshot() for i, core in self.dsps.items()},
                    sport_frames=list(self.sport_frames),
                    sport_input=list(self.sport_input), sport_output=list(self.sport_output),
                    sport_peers=dict(self.sport_peers), sport_latency_words=dict(self.sport_latency_words),
                    ipacs=[dict(mask=p.mask, maskd=p.maskd, aux_mask=p.aux_mask, pending_aux=p.pending_aux, pending=p.pending,
                                pending_d=p.pending_d, cir0=p.cir0, irq=p.irq,
                                commands=p.commands[-6:], tx_fifo=bytes(p.tx_fifo).hex(),
                                tx_packet=bytes(p.tx_packet).hex(),
                                mode_d=p.config[0x22], exmd1=p.config[0x23],
                                bearers=[dict(mode=p.config[0x72 + 16 * i],
                                    extension=b.extension, mask=p.maskb[i], pending=p.pending_b[i],
                                    tx_fifo=bytes(b.tx_fifo).hex(), tx_packet=bytes(b.tx_packet).hex(),
                                    transmitted=[frame.hex() for frame in b.transmitted],
                                    rx_length=b.rx_length, rx_status=b.rx_status, rx_busy=b.rx_busy)
                                    for i, b in enumerate(p.bearers)]) for p in self.ipacs],
                    controllers=[dict(index=i, bearers=2,
                        memory_offset=i * (0x11000 if self.reentrant else INSTANCE_SIZE),
                        shared_offset=(0x1000 + i * 0x11000 if self.reentrant else
                                       (i + 1) * INSTANCE_SIZE - SHARED_SIZE),
                        signature=int.from_bytes(
                            self.controller_memory(i, True)[0x1e:0x20], 'little'))
                        for i in range(4)])

    @staticmethod
    def _dsp_port(offset):
        for controller in range(4):
            for channel in range(2):
                base = 0x1800 + controller * 0x1000 + channel * 0x10
                if offset in (base, base + 8):
                    return controller * 2 + channel, offset == base + 8
        return None

    @staticmethod
    def _ipac_port(offset):
        controller = (offset >> 12) - 1
        low = offset & 0xFFF
        if 0 <= controller < 4 and low <= 0x224 and low % 4 == 0:
            return controller, low // 4
        return None

    def _core(self, index):
        if index not in self.dsps:
            self.dsps[index] = self._dsp_factory()
        return self.dsps[index]

    def read(self, bar, offset, size):
        self._validate(bar, offset, size)
        if bar in (2, 3) and not self.local_mapping:
            return (1 << (size * 8)) - 1
        if bar == 2:
            return int.from_bytes(self.ram[offset:offset + size], 'little')
        if bar == 0 and offset in (0, 0x18, 0xac) and size == 4:
            return ({0: 0xff000000, 0x18: 0x8a430143, 0xac: 0x0201000c}
                    if self.local_mapping else
                    {0: 0xfff00000, 0x18: 0x42430043, 0xac: 0x00200000})[offset]
        if bar == 0 and offset == 0x68 and size == 4:
            return 0x0f010000 | (self.interrupt_enable << 8) | (0x8000 if self.interrupt_pending and self.interrupt_enable & 8 else 0)
        if bar == 0 and offset == INTCSR and size == 1:
            return self.interrupt_enable | (0x80 if self.interrupt_pending and self.interrupt_enable & 8 else 0)
        if bar == 0 and offset == 0x6e and size == 2:
            return self.fpga.read() | self.bridge_control
        if bar == 3:
            ipac = self._ipac_port(offset)
            if size == 1 and ipac:
                return self.ipacs[ipac[0]].read(ipac[1])
            if offset == 0 and size in (1, 2):
                return self.device_reset & ((1 << (size * 8)) - 1)
            if size in (1, 2, 4) and offset in (0, RISC, IRQ_TEST, IPACX_CONFIG):
                return {0: self.device_reset, RISC: self.risc, IRQ_TEST: int(self.interrupt_pending),
                        IPACX_CONFIG: self.ipacx_config}[offset]
            port = self._dsp_port(offset)
            if size == 2 and port:
                return self._core(port[0]).read(port[1]) if self.device_reset & 1 else 0xFFFF
        raise NotImplementedError(f'unsupported BAR{bar} read {offset:#x}/{size}')

    def write(self, bar, offset, size, value):
        self._validate(bar, offset, size)
        if not 0 <= value < 1 << (size * 8):
            raise ValueError('value does not fit access width')
        if bar in (2, 3) and not self.local_mapping:
            return  # No local decode until EEPROM has restored bridge mappings.
        if bar == 2:
            self.ram[offset:offset + size] = value.to_bytes(size, 'little')
            return
        if bar == 0 and offset == 0x6e and size == 2:
            bridge_control = value & 0x6000
            if bridge_control == 0x6000:
                raise NotImplementedError('simultaneous PLX reset and EEPROM reload is unqualified')
            if self.cpu_running and not bridge_control & 0x4000:
                raise RuntimeError('halt the MIPS before FPGA configuration')
            was_done = self.fpga.done
            self.fpga.write(value & ~0x6000)
            previous_control = self.bridge_control
            self.bridge_control = bridge_control
            if bridge_control & 0x4000 and not previous_control & 0x4000:
                # A bridge reset removes local address decode; SDRAM and the
                # already configured FPGA survive the shipping reset sequence.
                previous_risc = self.risc
                self.risc = 0
                if self.cpu_control is not None:
                    self.cpu_control(previous_risc, 0)
                self.local_mapping = False
                self.interrupt_pending = False
            if previous_control & 0x2000 and not bridge_control & 0x2000:
                self.local_mapping = True
            if self.fpga.done and not was_done:
                self.risc = 0
                self.interrupt_pending = False
            return
        if bar == 0 and offset == 0x68 and size == 4:
            if value & ~0x0f01ff00:
                raise NotImplementedError('unqualified PLX interrupt control bits')
            self.interrupt_enable = (value >> 8) & 0x7f
            return
        if bar == 0 and offset == INTCSR and size == 1:
            self.interrupt_enable = value & 0x7F  # pending is device-owned
            return
        if bar == 3:
            ipac = self._ipac_port(offset)
            if size == 1 and ipac:
                self.ipacs[ipac[0]].write(ipac[1], value)
                return
            if offset == 0 and size in (1, 2, 4):
                for index, device in enumerate(self.ipacs):
                    if not value & (2 << index):
                        device.reset()
                if not value & 1:
                    for index, core in self.dsps.items():
                        core.reset()
                        self._fl0_seen[index] = 0
                self.device_reset = value
                return
            if offset == RISC and size == 4 and value in (0, 1, 3):
                if value == 3 and not self.fpga.done:
                    raise RuntimeError('cannot start MIPS with an unconfigured FPGA')
                previous = self.risc
                self.risc = value
                if self.cpu_control is not None:
                    self.cpu_control(previous, value)
                return
            if offset == IRQ_TEST and size in (1, 2, 4) and value in (0, 1):
                self.interrupt_pending = bool(value)
                return
            if offset == IPACX_CONFIG and size in (1, 2, 4):
                self.ipacx_config = value
                return
            port = self._dsp_port(offset)
            if size == 2 and port:
                if self.device_reset & 1:
                    self._core(port[0]).write(port[1], value)
                return
        raise NotImplementedError(f'unsupported BAR{bar} write {offset:#x}/{size}')

    def _validate(self, bar, offset, size):
        self._check_open()
        if size not in (1, 2, 4) or offset < 0 or offset % size:
            raise ValueError('access must be aligned and 1, 2 or 4 bytes')
        if bar == 2 and offset + size > MEMORY_SIZE:
            raise ValueError('SDRAM access outside BAR2')

    def poll_dsp_interrupts(self):
        for index, core in self.dsps.items():
            falls = core.lib.adsp2181_fl0_falls(core.cpu)
            # FL0-to-interrupt wiring remains unqualified. Retain edge
            # telemetry without inventing a connection to an IPAC input.
            self._fl0_seen[index] = falls

    def connect_sport1(self, first, second, latency_words=1):
        """Explicit raw-word peer wiring, with selectable serial-word delay.

        This API does not infer FPGA slot selection or physical B-channel
        ownership. The caller supplies the DSP endpoints being tested.
        """
        self._check_open()
        if first == second or any(type(i) is not int or not 0 <= i < 8
                                  for i in (first, second)):
            raise ValueError('SPORT endpoints must be distinct DSP indices 0..7')
        if type(latency_words) is not int or not 1 <= latency_words <= 64:
            raise ValueError('SPORT delay must be 1..64 words')
        if first in self.sport_peers or second in self.sport_peers:
            raise ValueError('SPORT endpoint already connected')
        from collections import deque
        self.sport_peers[first], self.sport_peers[second] = second, first
        for endpoint in (first, second):
            self.sport_latency_words[endpoint] = latency_words
            self._sport_delays[endpoint] = deque([0xffff] * (latency_words - 1))

    def clock_sport1(self, cycles=8192):
        """One raw word event per enabled DSP port; no B-channel slot synthesis.

        The caller owns wire words. Default all-ones is the measured idle
        input. IPAC setup and reset release must precede external clocks.
        """
        self._check_open()
        if cycles <= 0 or any(not 0 <= word <= 0xffff for word in self.sport_input):
            raise ValueError('SPORT words must be 16-bit and cycles positive')
        if not self.device_reset & 1:
            return
        outputs = {}
        for index, core in self.dsps.items():
            port = index // 2
            if not self.device_reset & (2 << port) or not self.ipacs[port].iom_configured:
                continue
            result = core.sport1(self.sport_input[index], cycles)
            if result is not None:
                outputs[index] = result & 0xffff
                self.sport_output[index] = result
                self.sport_frames[index] += 1
        # Commit after every core has sampled its input: iteration order
        # cannot let one direction see a newer frame than the other.
        for endpoint, peer in self.sport_peers.items():
            delay = self._sport_delays[endpoint]
            delay.append(outputs.get(peer, 0xffff))
            self.sport_input[endpoint] = delay.popleft()
        self.poll_dsp_interrupts()

    def clock_iom_frame(self, cycles_per_word=8192):
        """Clock the kernel's four 16-bit SPORT1 slots per PCM sample.

        Enabling a port joins at an IOM frame boundary, rather than at an
        arbitrary serial slot. Word-level peer delays still apply inside
        the frame. This is a functional schedule, not an oscillator model.
        """
        for _ in range(4):
            self.clock_sport1(cycles_per_word)

    def run_dsps(self, cycles):
        """Explicit scheduling only; cores still in IDMA boot hold do not run."""
        self._check_open()
        if cycles < 0:
            raise ValueError('cycles must be nonnegative')
        if not self.device_reset & 1:
            return 0
        elapsed = sum(core.run(cycles) for core in self.dsps.values())
        self.poll_dsp_interrupts()
        return elapsed

    def close(self):
        if not self.closed:
            self.pci.power_control = None
            for core in self.dsps.values():
                core.close()
            self.closed = True

    def __enter__(self):
        self._check_open()
        return self

    def __exit__(self, *args):
        self.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware-dir', type=Path,
                        help='stage te_dmlt.2q0 through .2q3; does not execute MIPS')
    args = parser.parse_args()
    with BriCard() as card:
        staged = []
        if args.firmware_dir:
            for controller in range(4):
                layout = card.stage_protocol(controller,
                    args.firmware_dir / f'te_dmlt.2q{controller}')
                staged.append(dict(controller=controller, base=layout.base,
                                   entry=layout.entry, size=layout.size))
        print(json.dumps(dict(**card.snapshot(), staged_protocols=staged), indent=2))


if __name__ == '__main__':
    main()
