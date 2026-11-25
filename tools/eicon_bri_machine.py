"""MIPS execution attached to BriCard SDRAM and actual board register model.

No PRI function addresses, return stubs or synthesized firmware responses.
Unimplemented device accesses stop the run with a diagnostic.
"""
from __future__ import annotations
import argparse
import ctypes
import json
import struct
from pathlib import Path
from eicon_bri_card import BriCard, MEMORY_SIZE


class BriMachine:
    WINDOW = 0x1F800000
    HIGH_ALIAS = 0x10000000
    BOOT_ALIAS = 0x1FC00000
    BOOT_ALIAS_SIZE = 0x1000
    RESET_PC = 0xBFC00000

    def __init__(self, card, cycles_per_access=200):
        from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN
        from unicorn import UC_HOOK_MEM_UNMAPPED
        self.card = card
        self.cycles_per_access = cycles_per_access
        self.fault = None
        self.processor_id = 0x2620
        self.processor_config = 0x26880
        self.memory_controller = {0x200: 0xAAAAAAAA, 0x204: 0xFFFFFFFF}
        self.uc = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        from unicorn.mips_const import UC_MIPS_REG_CP0_STATUS
        self.uc.reg_write(UC_MIPS_REG_CP0_STATUS, 0x1040EC04)
        # The host and MIPS observe the very same SDRAM, including queues.
        self._ram_owner = (ctypes.c_ubyte * MEMORY_SIZE).from_buffer(card.ram)
        self.uc.mem_map_ptr(0, MEMORY_SIZE, 7, ctypes.addressof(self._ram_owner))
        # Physically measured on SN12196: the reset aperture is a writable
        # alias of low SDRAM, not an invented boot-ROM instruction stub.
        self.uc.mem_map_ptr(self.BOOT_ALIAS, self.BOOT_ALIAS_SIZE, 7,
                            ctypes.addressof(self._ram_owner))
        # The spare also mirrors all SDRAM pages through physical 0x10000000.
        self.uc.mem_map_ptr(self.HIGH_ALIAS, MEMORY_SIZE, 7,
                            ctypes.addressof(self._ram_owner))
        self.pci_address = 0
        self.uc.mmio_map(0x1FB00000, 0x1000, self._pci_address_read, None,
                         self._pci_address_write, None)
        self.uc.mmio_map(0x1FB40000, 0x1000, self._pci_data_read, None,
                         self._pci_data_write, None)
        self.uc.mmio_map(self.WINDOW, 0x10000, self._read, None, self._write, None)
        self.uc.mmio_map(0xFFFFE000, 0x1000, self._controller_read, None,
                         self._controller_write, None)
        self.uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._unmapped)
        # Reset firmware uses kseg2 memory-controller registers. Give Unicorn
        # the same fixed translations as the existing 4BRI reset harness.
        from eicon_4bri_boot import entry_stub, TLB_WINDOWS
        scratch = 0x01000000
        setup = entry_stub(TLB_WINDOWS)
        self.uc.mem_map(scratch, 0x1000)
        self.uc.mem_write(scratch, setup)
        self.uc.emu_start(0xA0000000 + scratch,
                          0xA0000000 + scratch + len(setup))
        self.uc.mem_unmap(scratch, 0x1000)
        self.pc = self.RESET_PC
        self.initial_context = self.uc.context_save()
        self.reset_required = False
        card.cpu_control = self._cpu_control
        self.count = 0
        self.compare = None
        self.timer_pending = False
        self.interrupt_pc = None
        self.delivering_interrupt = False
        self.instructions = 0
        self.timer_interrupts = 0
        self.device_interrupts = 0
        self.iom_cycles = 0
        self.iom_frames = 0
        from unicorn import UC_HOOK_BLOCK, UC_HOOK_CODE
        self.uc.hook_add(UC_HOOK_BLOCK, self._clock_block)
        # Unicorn's MIPS Count does not advance with instruction execution.
        # Decode architectural Count accesses rather than hook firmware APIs.
        for offset, (word,) in enumerate(struct.iter_unpack('<I', card.ram)):
            if word & 0xFFE0FFFF in (0x40004800, 0x40804800, 0x40805800, 0x40006800,
                                       0x40007800, 0x40008000, 0x40808000):
                for segment in (0x80000000, 0xA0000000, 0x90000000, 0xB0000000):
                    address = segment + offset * 4
                    self.uc.hook_add(UC_HOOK_CODE, self._count_access, word,
                                     begin=address, end=address)
                if offset * 4 < self.BOOT_ALIAS_SIZE:
                    address = self.RESET_PC + offset * 4
                    self.uc.hook_add(UC_HOOK_CODE, self._count_access, word,
                                     begin=address, end=address)

    def _pci_address_read(self, uc, offset, size, user):
        if offset != 0 or size != 4:
            self._fail('unsupported PCI configuration address access')
            return 0
        return self.pci_address

    def _pci_address_write(self, uc, offset, size, value, user):
        if offset != 0 or size != 4 or value & ~0xfc:
            self._fail(f'unsupported PCI configuration selector {value:#x}')
            return
        self.pci_address = value

    def _pci_data_read(self, uc, offset, size, user):
        if size not in (1, 2, 4) or offset < 0 or offset % size or offset + size > 4:
            self._fail('PCI configuration data outside selected dword')
            return 0
        return self.card.pci.read(self.pci_address + offset, size)

    def _pci_data_write(self, uc, offset, size, value, user):
        if size not in (1, 2, 4) or offset < 0 or offset % size or offset + size > 4:
            self._fail('PCI configuration data outside selected dword')
            return
        self.card.pci.write(self.pci_address + offset, size, value)

    def _cpu_control(self, previous, value):
        if previous == 3 and value != 3:
            self.reset_required = True

    def _reset_cpu(self):
        self.uc.context_restore(self.initial_context)
        self.uc.ctl_remove_cache(0x80000000, 0x81000000)
        self.uc.ctl_remove_cache(0xA0000000, 0xA1000000)
        self.uc.ctl_remove_cache(0x90000000, 0x91000000)
        self.uc.ctl_remove_cache(0xB0000000, 0xB1000000)
        self.uc.ctl_remove_cache(self.RESET_PC, self.RESET_PC + self.BOOT_ALIAS_SIZE)
        self.pc = self.RESET_PC
        self.count = self.instructions = self.iom_cycles = self.iom_frames = 0
        self.timer_interrupts = self.device_interrupts = 0
        self.compare = None
        self.processor_config = 0x26880
        self.timer_pending = False
        self.reset_required = False

    def _clock_block(self, uc, address, size, user):
        if self.delivering_interrupt:
            return
        self.instructions += size // 4
        self.count = (self.count + size // 4) & 0xFFFFFFFF
        self.iom_cycles += size // 4
        while self.iom_cycles >= 6250:
            self.iom_cycles -= 6250
            self.iom_frames += 1
            for device in self.card.ipacs:
                device.serial_tick()
            self.card.clock_iom_frame()
        if self.compare is not None and (self.count - self.compare) & 0xFFFFFFFF < 0x80000000:
            self.timer_pending = True
        cause = self._cause()
        if cause:
            from unicorn.mips_const import UC_MIPS_REG_CP0_STATUS
            status = uc.reg_read(UC_MIPS_REG_CP0_STATUS)
            if status & 1 and status & cause & 0xff00 and not status & 6:
                self.interrupt_pc = address
                uc.emu_stop()

    def _cause(self):
        # Build122-11's mask table at 0x8027065c maps the four IPACs to
        # MIPS IP2, IP3, IP5 and IP6, independently of the host PCI IRQ.
        # Confirmed by isolated C/I IRQ/ack probes on spare SN12196.
        cause = 0x8000 if self.timer_pending else 0
        for device, mask in zip(self.card.ipacs, (0x400, 0x800, 0x2000, 0x4000)):
            if device.irq:
                cause |= mask
        return cause

    def _count_access(self, uc, address, size, word):
        from unicorn import mips_const
        register = getattr(mips_const, f'UC_MIPS_REG_{(word >> 16) & 31}')
        cp0 = (word >> 11) & 31
        if word & 0x00800000:
            if cp0 == 11:
                self.compare = uc.reg_read(register)
                self.timer_pending = False
            elif cp0 == 16:
                self.processor_config = uc.reg_read(register)
            else:
                self.count = uc.reg_read(register)
        else:
            value = {9: self.count, 13: self._cause(), 15: self.processor_id,
                     16: self.processor_config}[cp0]
            uc.reg_write(register, value)
        uc.reg_write(mips_const.UC_MIPS_REG_PC, address + 4)

    def _deliver_timer(self, resume_pc):
        # Architectural exception entry: EPC, Cause.IP7, Status.EXL, then
        # the firmware's own vector. No firmware handler is replaced.
        from eicon_4bri_boot import _lui, _ori, _mtc0, _mfc0
        from unicorn.mips_const import UC_MIPS_REG_PC, UC_MIPS_REG_CP0_STATUS
        vector = (self.RESET_PC + 0x380 if self.uc.reg_read(UC_MIPS_REG_CP0_STATUS) & (1 << 22)
                  else 0x80000380)
        if self.timer_pending:
            self.timer_interrupts += 1
        if self._cause() & 0x6c00:
            self.device_interrupts += 1
        code = []
        for value, register in ((resume_pc, 14), (self._cause(), 13)):
            code += [_lui(26, value >> 16), _ori(26, value), _mtc0(26, register)]
        code += [_mfc0(26, 12), _ori(26, 2), _mtc0(26, 12)]
        data = struct.pack('<' + 'I' * len(code), *code)
        scratch = 0x01000000
        self.delivering_interrupt = True
        self.uc.mem_map(scratch, 0x1000)
        try:
            self.uc.mem_write(scratch, data)
            self.uc.emu_start(0xA0000000 + scratch, 0xA0000000 + scratch + len(data))
        finally:
            self.uc.mem_unmap(scratch, 0x1000)
            self.delivering_interrupt = False
        self.uc.reg_write(UC_MIPS_REG_PC, vector)

    def _controller_read(self, uc, offset, size, user):
        if size != 4 or offset not in self.memory_controller:
            self._fail(f'unsupported memory-controller read {offset:#x}/{size}')
            return 0
        return self.memory_controller[offset]

    def _controller_write(self, uc, offset, size, value, user):
        if size != 4 or offset not in self.memory_controller:
            self._fail(f'unsupported memory-controller write {offset:#x}/{size}')
            return
        self.memory_controller[offset] = value

    def _fail(self, message):
        self.fault = dict(pc=self.current_pc(), reason=message)
        self.uc.emu_stop()

    def current_pc(self):
        from unicorn.mips_const import UC_MIPS_REG_PC
        return self.uc.reg_read(UC_MIPS_REG_PC)

    def _unmapped(self, uc, access, address, size, value, user):
        self._fail(f'unmapped access {address:#x}/{size}, type {access}')
        return False

    def _read(self, uc, offset, size, user):
        try:
            self.card.run_dsps(self.cycles_per_access)
            return self.card.read(3, offset, size)
        except (ValueError, NotImplementedError, RuntimeError) as exc:
            self._fail(str(exc))
            return 0

    def _write(self, uc, offset, size, value, user):
        try:
            self.card.write(3, offset, size, value)
            self.card.run_dsps(self.cycles_per_access)
        except (ValueError, NotImplementedError, RuntimeError) as exc:
            self._fail(str(exc))

    def run(self, instructions=100000, start=None):
        from unicorn import UcError
        if not self.card.cpu_running:
            raise RuntimeError('board CPU is held in reset')
        if instructions <= 0:
            raise ValueError('instruction budget must be positive')
        if self.reset_required:
            self._reset_cpu()
        self.fault = None
        pc = self.pc if start is None else start
        begin = self.instructions
        try:
            while self.instructions - begin < instructions:
                self.interrupt_pc = None
                self.uc.emu_start(pc, 0, count=max(1, instructions - (self.instructions - begin)))
                if self.interrupt_pc is None:
                    break
                self._deliver_timer(self.interrupt_pc)
                pc = self.current_pc()
        except UcError as exc:
            if self.fault is None:
                self.fault = dict(pc=self.current_pc(), reason=str(exc))
        self.pc = self.current_pc()
        return dict(pc=self.pc, fault=self.fault, board=self.card.snapshot(),
                    clock=dict(count=self.count, compare=self.compare, iom_frames=self.iom_frames,
                               timer_interrupts=self.timer_interrupts,
                               device_interrupts=self.device_interrupts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware-dir', type=Path)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--reentrant', action='store_true')
    parser.add_argument('--dsp-image', type=Path)
    parser.add_argument('--instructions', type=int, default=1000000)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--idi-probe', action='store_true')
    args = parser.parse_args()
    if bool(args.firmware_dir) == bool(args.bundle):
        parser.error('select exactly one of --firmware-dir or --bundle')
    with BriCard(reentrant=args.reentrant) as card:
        if args.bundle:
            image = args.bundle.read_bytes()
            if len(image) > MEMORY_SIZE:
                parser.error('bundle exceeds card SDRAM')
            card.ram[:len(image)] = image
        elif args.reentrant:
            if not args.dsp_image:
                parser.error('--reentrant requires --dsp-image')
            card.load_reentrant(args.firmware_dir / 'te_dmlt.2qm', args.dsp_image)
        else:
            for i in range(4):
                card.stage_protocol(i, args.firmware_dir / f'te_dmlt.2q{i}')
        machine = BriMachine(card)
        card.write(3, 0x200, 4, 1)
        card.write(3, 0x200, 4, 3)
        result = machine.run(args.instructions)
        boot_ready = all(c['signature'] == 0x4447 for c in result['board']['controllers'])
        if args.idi_probe and result['fault'] is None and boot_ready:
            from eicon_bri_host import BriHost
            from eicon_idi import sig_assign_payload, DSIG_ID, ASSIGN
            hosts = [BriHost(card, i) for i in range(4)]
            posted = [host.request(DSIG_ID, ASSIGN, parameters=sig_assign_payload())
                      for host in hosts]
            result = machine.run(2000000)
            result['idi'] = [dict(controller=i, posted=posted[i],
                                  returns=host.collect_returns())
                             for i, host in enumerate(hosts)]
        if args.bundle:
            from eicon_dsp_bench import read_results
            meta = json.loads(args.bundle.with_suffix('.meta.json').read_text())
            result['probe'] = read_results(card.ram, meta)
        result['completed'] = (result['fault'] is None and
                               (result['probe']['finished'] if args.bundle else boot_ready))
        if args.idi_probe:
            result['completed'] = result['completed'] and len(result.get('idi', [])) == 4 and all(
                entry['posted'] and len(entry['returns']) == 1 and
                entry['returns'][0]['code'] == 0xef for entry in result.get('idi', []))
        args.out.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({key: result[key] for key in ('pc', 'fault')}))
        return 0 if result['completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
