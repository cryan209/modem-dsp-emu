#!/usr/bin/env python3
"""Boot the recovered build-109 Analog/POTS MIPS image far enough to inspect it.

This is deliberately separate from the build-117 PRI shim: build 109 is a flat
absolute-address image (including reset/shared RAM), has no $gp, and uses a
single-channel Analog kernel. It maps the image at physical zero, patches the
host-selected card type bytes, executes the real protocol entry, and maps
zero-filled hardware/BSS pages on first access. It is the smallest executable
seam needed before porting IDI/DSPDAA dispatch anchors.
"""
from __future__ import annotations

import argparse
import collections
import struct
from pathlib import Path

from unicorn import (Uc, UcError, UC_ARCH_MIPS, UC_MODE_32,
                     UC_MODE_LITTLE_ENDIAN, UC_HOOK_BLOCK, UC_HOOK_CODE,
                     UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE,
                     UC_HOOK_MEM_FETCH_UNMAPPED, UC_HOOK_MEM_READ_UNMAPPED,
                     UC_HOOK_MEM_WRITE_UNMAPPED)
from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_A1,
                                UC_MIPS_REG_A2, UC_MIPS_REG_A3,
                                UC_MIPS_REG_PC, UC_MIPS_REG_RA,
                                UC_MIPS_REG_SP, UC_MIPS_REG_S0,
                                UC_MIPS_REG_S1,
                                UC_MIPS_REG_S2, UC_MIPS_REG_S6,
                                UC_MIPS_REG_T0, UC_MIPS_REG_T1,
                                UC_MIPS_REG_T4, UC_MIPS_REG_T5,
                                UC_MIPS_REG_T6, UC_MIPS_REG_T7,
                                UC_MIPS_REG_V0, UC_MIPS_REG_V1)

from eicon_dsp_stage import build_dsp_code_image
from eicon_mips_image import derive_layout

PAGE = 0x10000
# Staging the Analog Telindus archive moves the protocol heap beyond 4 MiB.
# The PCI card has substantially more SDRAM; 8 MiB covers the recovered image,
# its 861 KiB DSP archive and build-109's initialized controller objects.
IMAGE_RAM_SIZE = 0x800000
LOW_RAM_PHYS = 0x02000000
LOW_RAM_SIZE = 0x200000
RETURN_PHYS = 0x0F00000
RETURN_VIRT = 0x80F00000
TLB_STUB_PHYS = 0x3F0000
TLB_STUB_VIRT = 0xA03F0000
MGMT_SCRATCH_PHYS = 0x3E0000
MGMT_SCRATCH_VIRT = 0x803E0000
POST_TLB_WIPE = 0xA00110E4

# The build-109 image uses the standard Eicon PR_RAM host/firmware queue at
# physical 0x1000. Unlike its code addresses, this interface did not move
# between the Analog and PRI products.
PR_RAM = 0x1000
PR_NEXT_REQ = 0x00
PR_NEXT_RC = 0x02
PR_NEXT_IND = 0x04
PR_REQ_INPUT = 0x06
PR_REQ_OUTPUT = 0x07
PR_INT = 0x09
PR_RC_OUTPUT = 0x0B
PR_IND_OUTPUT = 0x0C
PR_SIGNATURE = 0x1E
PR_BUFFERS = 0x20
REQ_REQ = 0x02
REQ_ID = 0x03
REQ_CH = 0x04
REQ_REFERENCE = 0x06
REQ_XBUFFER = 0x10
REQ_XDATA = 0x12


def _phys(address: int) -> int:
    return address & 0x1FFFFFFF


def _round(value: int, unit: int = PAGE) -> int:
    return (value + unit - 1) & -unit


class AnalogMipsBoot:
    def __init__(self, image: Path, card_type: int = 77,
                 dsp_combifile: Path | None = None):
        self.image = image
        self.layout = derive_layout(image)
        if self.layout.base != 0x80000000 or self.layout.gp is not None:
            raise ValueError(f"{image} is not a flat absolute-address image")
        self.uc = Uc(UC_ARCH_MIPS, UC_MODE_LITTLE_ENDIAN | UC_MODE_32)
        data = bytearray(image.read_bytes())
        # Host loader patches initial task and card type in the image header.
        data[0x68] = 1
        data[0x69] = card_type & 0xFF
        # analog_protocol_load() publishes the first free address as the DSP
        # download base. The scheduler deliberately treats a zero header word
        # as fatal before entering card initialization.
        protocol_end = struct.unpack_from('<I', data, 0x7C)[0]
        dsp_code_base = (protocol_end + 3) & ~3
        struct.pack_into('<I', data, 0x6C, dsp_code_base)
        # Unicorn cannot translate the bootstrap's CPU-local kseg2 stores
        # before the firmware wipes the TLB. They only program cache timing;
        # the board-visible state starts after the wipe.
        struct.pack_into('<I', data, 0x11010, 0)
        struct.pack_into('<I', data, 0x11020, 0)
        # Keep memset as a host-accelerated libc seam. Its entry hook performs
        # the write; this two-instruction body returns without changing PC from
        # inside a Unicorn code callback (which destabilizes repeated TLB
        # refills on MIPS).
        struct.pack_into('<II', data, 0x106840, 0x03E00008, 0x00801021)
        # Unicorn does not advance CP0 Count. Leave the firmware wrapper's
        # return in place but suppress mfc0 so the code hook can supply it.
        struct.pack_into('<I', data, 0x11200, 0)
        # Turn the card's indexed 16-bit register helpers into host seams. The
        # hooks below retain register state and can be connected to DSPDAA.
        struct.pack_into('<II', data, 0x104418, 0x03E00008, 0)
        struct.pack_into('<II', data, 0x10444C, 0x03E00008, 0)
        # Channel bring-up waits for a running FPGA/DSPDAA core. Bulk IDMA is
        # captured separately by the direct MMIO hooks; report that one core is
        # available until the captured kernel is attached to the live ADSP.
        struct.pack_into('<II', data, 0x139130, 0x03E00008, 0x24020001)
        mapped = _round(max(IMAGE_RAM_SIZE, len(data),
                            _phys(self.layout.stack_top) + PAGE))
        self.uc.mem_map(0, mapped)
        self.uc.mem_write(0, bytes(data))
        # diva_configure_protocol() writes hardware-dependent values into the
        # first 256 bytes of shared RAM after loading the protocol. Bit 7 says
        # a Telindus DSP image follows; card type is repeated here separately
        # from the image header.
        # diva_configure_analog_protocol() always selects protocol 34/POTS.
        # Bit 7 marks the DMLT selector as valid. Leaving byte 19 zero boots
        # this multi-protocol image as its default CAS personality, which can
        # assign a modem but can never enter the Analog senddialtone path.
        self.uc.mem_write(PR_RAM + 19, bytes((0x80 | 34,)))
        self.uc.mem_write(PR_RAM + 22, bytes((0x80,)))
        self.uc.mem_write(PR_RAM + 26, bytes((card_type & 0xFF,)))
        # analog_telindus_load() stages the portable DSP archive immediately
        # after the protocol image before releasing MIPS reset. Without this
        # table the protocol boots but registers no modem/tone resources.
        combifile = dsp_combifile or image.with_name('dspdload.bin')
        self.dsp_code_image = None
        if combifile.is_file():
            self.dsp_code_image = build_dsp_code_image(
                combifile, card_type=card_type, base_addr=dsp_code_base)
            end = _phys(self.dsp_code_image.end_addr)
            if end > mapped:
                raise ValueError(f'DSP image ends at 0x{end:x}, beyond mapped RAM')
            self.uc.mem_write(_phys(dsp_code_base), self.dsp_code_image.data)
            # PCINIT_DSP_IMAGE_LENGTH is a type/length/value tuple beginning at
            # the driver's PCINIT_OFFSET (224), followed by the terminator.
            # It moves the protocol heap past the staged archive; omitting it
            # makes the controller objects overwrite the DSP descriptors.
            self.uc.mem_write(PR_RAM + 224,
                              bytes((0x34, 4))
                              + struct.pack('<I', len(self.dsp_code_image.data))
                              + b'\0')
        # Build 109 has two address spaces at the same numeric offsets: its
        # image executes through kseg0/1 while allocator pointers are in useg.
        # The card maps useg to separate working RAM. Identity mapping it onto
        # physical image storage clears live code during normal heap setup.
        self.uc.mem_map(LOW_RAM_PHYS, LOW_RAM_SIZE)
        self.uc.mem_map(RETURN_PHYS, PAGE)
        self.uc.mem_write(RETURN_PHYS, struct.pack('<II', 0x03E00008, 0))
        self.mapped = {(0, mapped), (LOW_RAM_PHYS, LOW_RAM_SIZE),
                       (RETURN_PHYS, PAGE)}
        self.blocks = []
        self.self_loop = None
        self.null_callbacks = 0
        self.instructions = []
        self.clock_object = None
        self.clock_root = None
        self.clock_ticks = 0
        self.cp0_count = 0
        self.hw_registers: dict[tuple[int, int], int] = {}
        self.idma_addresses: dict[int, int] = {}
        self.dsp_pm: dict[int, list[int]] = {}
        self.dsp_dm: dict[int, list[int]] = {}
        self._pm_half: dict[int, int | None] = {}
        self.hw_reads: collections.Counter[tuple[int, int]] = collections.Counter()
        self.hw_read_callers: collections.Counter[tuple[int, int, int]] = collections.Counter()
        self.hw_writes: collections.Counter[tuple[int, int]] = collections.Counter()
        self.hw_write_log: list[tuple[int, int, int]] = []
        self.mailbox_read = None
        self.mailbox_write = None
        self.analog_line = None
        self.bootstrap_instructions = []
        self.memset_range = None
        self.memsets = []
        self.object_pointer = None
        self.property_pointer = None
        self.scheduler_pointer = None
        self.property_element = None
        self.unmapped_fault = None
        self.signaling_entity: int | None = None
        self.dial_generator_calls = 0
        self.native_download_requests: list[tuple[int, int]] = []
        self.native_download_blocks: list[int] = []
        self._native_assign_object = 0
        self._native_download_completions: set[int] = set()
        self._boot_ack_keys: set[tuple[int, int]] = set()
        self.trace_formats: collections.Counter[str] = collections.Counter()
        self.trace_records: list[tuple[str, int, int, int]] = []
        self.last_block = None
        self.block_repeats = 0
        self.uc.hook_add(UC_HOOK_MEM_FETCH_UNMAPPED, self._unmapped)
        self.uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED, self._unmapped)
        self.uc.hook_add(UC_HOOK_MEM_WRITE_UNMAPPED, self._unmapped)
        self.uc.hook_add(UC_HOOK_BLOCK, self._block)
        self.uc.hook_add(UC_HOOK_CODE, self._return,
                         begin=RETURN_VIRT, end=RETURN_VIRT)
        # Hardware service vectors are installed later by the board support
        # package. During this standalone boot an indirect call can encounter
        # a still-NULL vector. Treat it as an empty hardware callback, count it
        # prominently, and continue; this is a bounded boot seam, not guest
        # state silently invented in RAM.
        self.uc.hook_add(UC_HOOK_CODE, self._null_callback, begin=0, end=0)
        self.uc.hook_add(UC_HOOK_CODE, self._clock,
                         begin=0x80107284, end=0x80107284)
        self.uc.hook_add(UC_HOOK_CODE, self._count,
                         begin=0x80011200, end=0x80011200)
        self.uc.hook_add(UC_HOOK_CODE, self._hw_read,
                         begin=0x80104418, end=0x80104418)
        self.uc.hook_add(UC_HOOK_CODE, self._hw_write,
                         begin=0x8010444C, end=0x8010444C)
        self.uc.hook_add(UC_HOOK_CODE, self._dial_generator,
                         begin=0x800206D4, end=0x800206D4)
        self.uc.hook_add(UC_HOOK_CODE, self._cas_line_sensors,
                         begin=0x800F4414, end=0x800F4414)
        self.uc.hook_add(UC_HOOK_CODE, self._native_assign_entry,
                         begin=0x80115AC4, end=0x80115AC4)
        self.uc.hook_add(UC_HOOK_CODE, self._native_download_request,
                         begin=0x80116408, end=0x80116408)
        self.uc.hook_add(UC_HOOK_CODE, self._trace_printf,
                         begin=0x80105D64, end=0x80105D64)
        # Native segmented downloads use the memory-mapped IDMA ports directly
        # (+8 address, +0 data), bypassing the indexed helper functions above.
        # Keep these as pure-Python shadow cores while Unicorn is executing;
        # calling the native ADSP library recursively from a Unicorn hook
        # crashes on macOS.
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._dsp_mmio_write,
                         begin=0x1F800000, end=0x1F80FFFF)
        self.uc.hook_add(UC_HOOK_MEM_READ, self._dsp_mmio_read,
                         begin=0x1F800000, end=0x1F80FFFF)
        self.uc.hook_add(UC_HOOK_CODE, self._bootstrap_instruction,
                         begin=0xA0011004, end=0xA00111C0)
        self.uc.hook_add(UC_HOOK_CODE, self._object_init,
                         begin=0x80107568, end=0x80107568)
        self.uc.hook_add(UC_HOOK_CODE, self._memset_entry,
                         begin=0x80106840, end=0x80106840)
        self.uc.hook_add(UC_HOOK_CODE, self._property_parser,
                         begin=0x80093C40, end=0x80093C40)
        self.uc.hook_add(UC_HOOK_CODE, self._property_read,
                         begin=0x80093C8C, end=0x80093C8C)
        self.uc.hook_add(UC_HOOK_CODE, self._property_element_read,
                         begin=0x80094324, end=0x80094324)
        self.uc.hook_add(UC_HOOK_CODE, self._scheduler_read,
                         begin=0x80105E3C, end=0x80105E3C)
        self.uc.hook_add(UC_HOOK_CODE, self._mailbox_ready,
                         begin=0x80139FEC, end=0x80139FEC)

    def _install_low_tlb(self) -> None:
        """Map the card's 2 MiB useg work RAM after firmware's TLB wipe."""
        def lui(rt, value):
            return 0x3C000000 | (rt << 16) | ((value >> 16) & 0xFFFF)
        def ori(rt, value):
            return 0x34000000 | (rt << 21) | (rt << 16) | (value & 0xFFFF)
        def mtc0(rt, rd):
            return 0x40800000 | (rt << 16) | (rd << 11)

        flags = (2 << 3) | (1 << 2) | (1 << 1) | 1
        values = (
            (0, 0),                    # Index
            (5, 0x001FE000),           # 1 MiB pages, 2 MiB pair
            (10, 0),                   # EntryHi: useg zero
            (2, (LOW_RAM_PHYS >> 12) << 6 | flags),
            (3, ((LOW_RAM_PHYS + 0x100000) >> 12) << 6 | flags),
        )
        words = []
        for rd, value in values:
            words.extend((lui(8, value), ori(8, value), mtc0(8, rd)))
        words.append(0x42000002)       # tlbwi
        code = b''.join(struct.pack('<I', word) for word in words)
        self.uc.mem_write(TLB_STUB_PHYS, code)
        self.uc.emu_start(TLB_STUB_VIRT, TLB_STUB_VIRT + len(code))

    def _unmapped(self, uc, access, address, size, value, user):
        physical = _phys(address)
        page = physical & -PAGE
        try:
            uc.mem_map(page, PAGE)
        except UcError as exc:
            self.unmapped_fault = (access, address, physical, page, str(exc))
            return False
        self.mapped.add((page, PAGE))
        return True

    def _block(self, uc, address, size, user):
        self.blocks.append(address)
        if len(self.blocks) > 64:
            del self.blocks[:-64]
        if address == self.last_block:
            self.block_repeats += 1
            # Large memset loops legitimately execute one block tens of
            # thousands of times while clearing the flat image's BSS. Only
            # treat a much longer stationary block run as a firmware wait.
            if self.block_repeats >= 100_000:
                self.self_loop = address
                uc.emu_stop()
        else:
            self.last_block = address
            self.block_repeats = 0

    @staticmethod
    def _return(uc, address, size, user):
        uc.emu_stop()

    def _null_callback(self, uc, address, size, user):
        self.null_callbacks += 1
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    @staticmethod
    def _property_parser(uc, address, size, user):
        pass

    @staticmethod
    def _mailbox_ready(uc, address, size, user):
        mailbox = uc.reg_read(UC_MIPS_REG_A0)
        # a0 is loaded by the instruction at this address, so obtain the
        # channel object's hardware descriptor through s1 first.
        descriptor = uc.reg_read(UC_MIPS_REG_S1)
        mailbox = struct.unpack('<I', uc.mem_read(_phys(descriptor), 4))[0]
        physical = _phys(mailbox + 0xD8E)
        status = struct.unpack('<H', uc.mem_read(physical, 2))[0]
        uc.mem_write(physical, struct.pack('<H', status | 0x0200))

    def _property_element_read(self, uc, address, size, user):
        self.property_element = (uc.reg_read(UC_MIPS_REG_S0),
                                 uc.reg_read(UC_MIPS_REG_S1),
                                 uc.reg_read(UC_MIPS_REG_A1))

    def _scheduler_read(self, uc, address, size, user):
        pointer = uc.reg_read(UC_MIPS_REG_S2)
        self.scheduler_pointer = pointer

    def _property_read(self, uc, address, size, user):
        pointer = uc.reg_read(UC_MIPS_REG_S1)
        self.property_pointer = pointer

    def _alloc(self, uc, address, size, user):
        """Build-109 allocator seam preserving its native low pointers."""
        size = (uc.reg_read(UC_MIPS_REG_A0) + 3) & ~3
        output = uc.reg_read(UC_MIPS_REG_A1)
        start = struct.unpack('<I', uc.mem_read(0x112C4, 4))[0]
        end = struct.unpack('<I', uc.mem_read(0x112C8, 4))[0]
        if start + size > end:
            pointer = 0
        else:
            pointer = start
            uc.mem_write(0x112C4, struct.pack('<I', start + size))
        uc.mem_write(_phys(output), struct.pack('<I', pointer))
        uc.reg_write(UC_MIPS_REG_V0, 0)
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def _object_init(self, uc, address, size, user):
        self.object_pointer = uc.reg_read(UC_MIPS_REG_A1)

    def _memset_entry(self, uc, address, size, user):
        target = uc.reg_read(UC_MIPS_REG_A0)
        value = uc.reg_read(UC_MIPS_REG_A1) & 0xFF
        length = uc.reg_read(UC_MIPS_REG_A2)
        self.memset_range = (target, target + length)
        self.memsets.append((target, value, length,
                             uc.reg_read(UC_MIPS_REG_RA)))
        del self.memsets[:-16]
        if length and value:
            physical = (LOW_RAM_PHYS + target if target < 0x80000000
                        else _phys(target))
            uc.mem_write(physical, bytes((value,)) * length)

    def _trace_printf(self, uc, address, size, user):
        # Preserve the format string as execution evidence without attempting
        # guest varargs formatting or walking the hardware trace scheduler.
        pointer = uc.reg_read(UC_MIPS_REG_A0)
        try:
            raw = bytes(uc.mem_read(_phys(pointer), 160)).split(b'\0', 1)[0]
            text = raw.decode('ascii', 'replace')
            if text:
                self.trace_formats[text] += 1
                self.trace_records.append((text,
                                           uc.reg_read(UC_MIPS_REG_A1),
                                           uc.reg_read(UC_MIPS_REG_A2),
                                           uc.reg_read(UC_MIPS_REG_A3)))
                del self.trace_records[:-256]
        except UcError:
            pass
        uc.reg_write(UC_MIPS_REG_V0, 0)
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    @staticmethod
    def _dsp_block_for_mmio(address: int) -> tuple[int, int] | None:
        for block in (0x1F801800, 0x1F802800, 0x1F803800, 0x1F804800):
            offset = address - block
            if 0 <= offset < 0x100:
                return block | 0xA0000000, offset
        return None

    def _dsp_mmio_write(self, uc, access, address, size, value, user):
        decoded = self._dsp_block_for_mmio(address)
        if decoded is None:
            return
        block, port = decoded
        value &= 0xFFFF
        if port == 8:
            self.idma_addresses[block] = value
            self._pm_half[block] = None
            return
        if port != 0:
            return
        idma = self.idma_addresses.get(block, 0)
        if idma & 0x4000:
            memory = self.dsp_dm.setdefault(block, [0] * 0x4000)
            memory[idma & 0x3FFF] = value
            self.idma_addresses[block] = 0x4000 | ((idma + 1) & 0x3FFF)
        else:
            memory = self.dsp_pm.setdefault(block, [0] * 0x4000)
            first = self._pm_half.get(block)
            if first is None:
                self._pm_half[block] = value
            else:
                memory[idma & 0x3FFF] = (first << 8) | (value & 0xFF)
                self.idma_addresses[block] = (idma + 1) & 0x3FFF
                self._pm_half[block] = None

    def _dsp_mmio_read(self, uc, access, address, size, value, user):
        decoded = self._dsp_block_for_mmio(address)
        if decoded is None:
            return
        block, port = decoded
        if port != 0:
            return
        idma = self.idma_addresses.get(block, 0)
        memory = (self.dsp_dm.setdefault(block, [0] * 0x4000)
                  if idma & 0x4000 else
                  self.dsp_pm.setdefault(block, [0] * 0x4000))
        result = memory[idma & 0x3FFF] & 0xFFFF
        uc.mem_write(address, struct.pack('<H', result))

    def _shadow_idma_write(self, block: int, register: int, value: int) -> None:
        self.idma_addresses[block] = register
        physical = (block & 0x1FFFFFFF)
        self._dsp_mmio_write(self.uc, 0, physical, 2, value, None)

    def _shadow_idma_read(self, block: int, register: int) -> int:
        memory = (self.dsp_dm.setdefault(block, [0] * 0x4000)
                  if register & 0x4000 else
                  self.dsp_pm.setdefault(block, [0] * 0x4000))
        return memory[register & 0x3FFF] & 0xFFFF

    def _hw_read(self, uc, address, size, user):
        base = uc.reg_read(UC_MIPS_REG_A0)
        register = uc.reg_read(UC_MIPS_REG_A1) & 0xFFFF
        key = (base, register)
        self.hw_reads[key] += 1
        self.hw_read_callers[(base, register,
                              uc.reg_read(UC_MIPS_REG_RA))] += 1
        value = (self.mailbox_read(base, register)
                 if self.mailbox_read is not None else
                 self.hw_registers.get(key,
                                       self._shadow_idma_read(base, register)))
        uc.reg_write(UC_MIPS_REG_V0, value & 0xFFFF)

    def _hw_write(self, uc, address, size, user):
        base = uc.reg_read(UC_MIPS_REG_A0)
        register = uc.reg_read(UC_MIPS_REG_A1) & 0xFFFF
        value = uc.reg_read(UC_MIPS_REG_A2) & 0xFFFF
        key = (base, register)
        self.hw_writes[key] += 1
        self.hw_write_log.append((base, register, value))
        # A running ADSP kernel turns the DSPDAA/TIKRNL start token around in
        # the indexed mailbox. This is the hardware response used both during
        # board initialization (0x80139130) and later task completion.
        response = 0xA5A5 if value == 0x5A5A else value
        self.hw_registers[key] = response
        if self.mailbox_write is not None:
            self.mailbox_write(base, register, value)
        else:
            self._shadow_idma_write(base, register, response)

    def set_mailbox_handlers(self, read=None, write=None) -> None:
        self.mailbox_read = read
        self.mailbox_write = write

    def _count(self, uc, address, size, user):
        self.cp0_count = (self.cp0_count + 10_000) & 0xFFFFFFFF
        uc.reg_write(UC_MIPS_REG_V0, self.cp0_count)

    def _clock(self, uc, address, size, user):
        # The routine combines CP0 count with per-controller calibration and
        # assumes the hardware scheduler's current-object pointer is live.
        # Supply a monotonic millisecond-scale value until that scheduler is
        # modeled; no state-machine decision is forced here.
        self.clock_ticks += 1
        uc.reg_write(UC_MIPS_REG_V0, self.clock_ticks)
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def _bootstrap_instruction(self, uc, address, size, user):
        self.bootstrap_instructions.append(
            (address, uc.reg_read(UC_MIPS_REG_T0)))
        if len(self.bootstrap_instructions) > 16:
            del self.bootstrap_instructions[:-16]

    def _instruction(self, uc, address, size, user):
        self.instructions.append(address)
        if address == 0x801072A8:
            self.clock_root = uc.reg_read(UC_MIPS_REG_V0)
        if address == 0x801072AC:
            self.clock_object = uc.reg_read(UC_MIPS_REG_V1)
        if len(self.instructions) > 16:
            del self.instructions[:-16]

    def run(self, max_insns: int = 5_000_000, reset: bool = True) -> None:
        self.uc.reg_write(UC_MIPS_REG_SP, self.layout.stack_top)
        self.uc.reg_write(UC_MIPS_REG_RA, RETURN_VIRT)
        self.uc.reg_write(UC_MIPS_REG_A0, 0)
        self.uc.reg_write(UC_MIPS_REG_A1, 0)
        self.uc.reg_write(UC_MIPS_REG_A2, 0)
        self.uc.reg_write(UC_MIPS_REG_A3, 0)
        # The flat reset/bootstrap initializes the allocator before calling
        # the protocol entry. Calling layout.entry directly leaves the global
        # controller root NULL and eventually reads the reset instruction as
        # an object pointer.
        if reset:
            self.uc.emu_start(self.layout.base, POST_TLB_WIPE,
                              count=max_insns)
            if self.uc.reg_read(UC_MIPS_REG_PC) != POST_TLB_WIPE:
                return
            self._install_low_tlb()
            start = POST_TLB_WIPE
        else:
            self._install_low_tlb()
            start = self.layout.entry
        self.uc.emu_start(start, RETURN_VIRT, count=max_insns)

    def _native_assign_entry(self, uc, address, size, user_data) -> None:
        self._native_assign_object = uc.reg_read(UC_MIPS_REG_A0)

    def _native_download_request(self, uc, address, size, user_data) -> None:
        """Record the channel object and requested portable download ID."""
        download = uc.reg_read(UC_MIPS_REG_S6) & 0xFFFF
        request = (self._native_assign_object, download)
        duplicate = (self.native_download_requests
                     and self.native_download_requests[-1] == request)
        if not duplicate:
            self.native_download_requests.append(request)
        # request[0] is the selected native channel descriptor; its +0x10 word
        # is the physical DSP block. Channel numbering is board-wiring order,
        # not ascending MMIO order (Analog channel 1 is 0xbf804800 here).
        channel = 0
        if self._native_assign_object:
            descriptor = struct.unpack(
                '<I', self.uc.mem_read(_phys(self._native_assign_object), 4))[0]
            if descriptor:
                channel = struct.unpack(
                    '<I', self.uc.mem_read(_phys(descriptor + 0x10), 4))[0]
        self.native_download_blocks.append(channel)
        # Return to the host at the asynchronous boundary. Continuing the
        # scheduler in this same slice lets CAS timeout/release the request
        # before its loader callback can run. The callback itself first writes
        # the protocol's 0x5a5a start token; the selected ADSP must turn that
        # into 0xa5a5 before its verification loop.
        if download == 0x0258 and channel and not duplicate:
            uc.emu_stop()

    def complete_native_download(self, channel: int) -> int:
        """Run build-109's selected-channel download completion callback.

        `dsp_assign` installs 0x80111ec0 in request+0x10, but that is the
        deferred free callback used by dsp_release.  The successful loader
        callback is 0x80138fec: it verifies the selected core's 0xa5a5 boot
        mailbox via 0x801182e8 and advances the channel through 0x8012b7cc.
        """
        # dsp_assign's request wrapper points at the actual channel object in
        # word zero. The adapter callback consumes that object, not the wrapper
        # whose +0x10 is its deferred release function.
        channel_object = struct.unpack(
            '<I', self.uc.mem_read(_phys(channel), 4))[0]
        block = struct.unpack(
            '<I', self.uc.mem_read(_phys(channel_object + 0x10), 4))[0]
        # 0x801182e8 writes 0x5a5a and immediately polls for 0xa5a5. Model the
        # running kernel's boot response at that exact write, without invoking
        # ctypes recursively from the Unicorn hook.
        # The selected kernel's boot command register is derived from its
        # descriptor and is 0x3fff for TIKRNL81.ANA (not the transient DM0
        # mailbox used by the earlier discovery seam).
        key = (block, 0x3FFF)
        old = self.hw_registers.get(key)
        read_handler, write_handler = self.mailbox_read, self.mailbox_write
        self._boot_ack_keys.add(key)
        # This callback runs between emulator slices. Keep it on the Python
        # shadow mailbox so no ADSP ctypes call is made recursively from a
        # Unicorn hook.
        self.mailbox_read = self.mailbox_write = None
        # The shadow owns the exact MMIO-captured bytes but not the transient
        # loader bookkeeping object consumed by 0x80111738. Confirm attachment
        # only for this callback; native board initialization remains separate.
        def attached(uc, address, size, user_data):
            uc.reg_write(UC_MIPS_REG_V0, 1)
            uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))
        validation_hook = self.uc.hook_add(
            UC_HOOK_CODE, attached, begin=0x80111738, end=0x80111738)
        try:
            result = self.call(0x80138FEC, channel_object, 0,
                               max_insns=2_000_000)
        finally:
            self.uc.hook_del(validation_hook)
            self.mailbox_read, self.mailbox_write = read_handler, write_handler
            self._boot_ack_keys.discard(key)
            if old is None:
                self.hw_registers.pop(key, None)
            else:
                self.hw_registers[key] = old
        if result:
            self._native_download_completions.add(channel)
        return result

    def _dial_generator(self, uc, address, size, user_data) -> None:
        """Count entries to native POTS senddialtone()."""
        self.dial_generator_calls += 1

    def _write8(self, address: int, value: int) -> None:
        self.uc.mem_write(address, bytes((value & 0xFF,)))

    def _write16(self, address: int, value: int) -> None:
        self.uc.mem_write(address, struct.pack('<H', value & 0xFFFF))

    def post_idi_request(self, request: int, entity: int, channel: int,
                         payload: bytes, reference: int = 0) -> int:
        """Submit one request through the card's native PR_RAM ring."""
        offset = struct.unpack('<H', self.uc.mem_read(PR_RAM + PR_NEXT_REQ, 2))[0]
        buffer = PR_RAM + PR_BUFFERS + offset
        next_offset = struct.unpack('<H', self.uc.mem_read(buffer, 2))[0]
        self._write8(buffer + REQ_REQ, request)
        self._write8(buffer + REQ_ID, entity)
        self._write8(buffer + REQ_CH, channel)
        self._write16(buffer + REQ_REFERENCE, reference)
        self._write16(buffer + REQ_XBUFFER, len(payload))
        self.uc.mem_write(buffer + REQ_XDATA, payload)
        self._write16(PR_RAM + PR_NEXT_REQ, next_offset)
        count = self.uc.mem_read(PR_RAM + PR_REQ_INPUT, 1)[0]
        self._write8(PR_RAM + PR_REQ_INPUT, count + 1)
        return offset

    def drain_idi_return_codes(self) -> list[tuple[int, int, int, int]]:
        """Consume native return codes as the host driver does."""
        count = self.uc.mem_read(PR_RAM + PR_RC_OUTPUT, 1)[0]
        offset = struct.unpack('<H', self.uc.mem_read(PR_RAM + PR_NEXT_RC, 2))[0]
        result = []
        for _ in range(count):
            buffer = PR_RAM + PR_BUFFERS + offset
            code = self.uc.mem_read(buffer + 2, 1)[0]
            if code:
                result.append((code,
                               self.uc.mem_read(buffer + 3, 1)[0],
                               self.uc.mem_read(buffer + 4, 1)[0],
                               struct.unpack('<H', self.uc.mem_read(buffer + 6, 2))[0]))
                self._write8(buffer + 2, 0)
            offset = struct.unpack('<H', self.uc.mem_read(buffer, 2))[0]
        self._write8(PR_RAM + PR_RC_OUTPUT, 0)
        self._write8(PR_RAM + PR_INT, 0)
        return result

    def assign_signaling(self) -> int | None:
        """Create the tty modem's native signalling entity."""
        import eicon_idi
        if self.signaling_entity is not None:
            return self.signaling_entity
        self.post_idi_request(eicon_idi.ASSIGN, eicon_idi.DSIG_ID, 0,
                              eicon_idi.sig_assign_payload())
        for _ in range(8):
            self.step(200_000)
            for code, entity, _channel, _reference in self.drain_idi_return_codes():
                if code == eicon_idi.ASSIGN_OK:
                    self.signaling_entity = entity
                    return entity
        return None

    def request_outgoing_call(self, number: str) -> bool:
        """Deliver ATD to build-109 as its native modem CALL_REQ."""
        import eicon_idi
        entity = self.assign_signaling()
        if entity is None:
            return False
        self.post_idi_request(eicon_idi.CALL_REQ, entity, 0,
                              eicon_idi.call_req_payload(number))
        return True

    def step(self, max_insns: int = 50_000) -> None:
        """Advance the suspended native POTS scheduler by one bounded slice."""
        self.self_loop = None
        self.block_repeats = 0
        self.last_block = None
        pc = self.uc.reg_read(UC_MIPS_REG_PC)
        self.uc.emu_start(pc, RETURN_VIRT, count=max_insns)

    def _cas_line_sensors(self, uc, address, size, user_data) -> None:
        """Publish the idle FXO state on CAS's native DAA sensor planes.

        CAS has just loaded root+0x1294+8..11 into t4..t7. Hardware reset leaves
        the shadow planes at 0xff, which CAS interprets as the unavailable
        0xf nibble. Exchange battery with an idle loop is nibble zero. Keep the
        reset value for a disconnected line so native cable/trunk handling
        remains intact.
        """
        if self.line_in_service():
            mask = ~1
            for register in (UC_MIPS_REG_T4, UC_MIPS_REG_T5,
                             UC_MIPS_REG_T6, UC_MIPS_REG_T7):
                uc.reg_write(register, uc.reg_read(register) & mask)

    def attach_analog_line(self, line) -> None:
        """Attach the physical line model used by native POTS supervision."""
        self.analog_line = line

    def line_in_service(self, channel: int = 1) -> bool:
        """Return the bounded DAA L1 result for one physical FXO port."""
        return bool(self.analog_line is not None and self.analog_line.in_service)

    def set_hook_input(self, off_hook: bool, channel: int = 1) -> None:
        """Publish the DAA hook sensor bits consumed by rxhook/POTS logic."""
        if not 1 <= channel <= 8:
            raise ValueError('channel must be 1..8')
        root = struct.unpack('<I', self.uc.mem_read(0x112C0, 4))[0]
        sensors = struct.unpack('<I', self.uc.mem_read(_phys(root + 0x1294), 4))[0]
        if not sensors:
            raise RuntimeError('DAA sensor block is not initialized')
        mask = 1 << ((channel - 1) & 7)
        # The native rxhook callback composes these four per-channel sensor
        # planes into a nibble. Plane zero is the receive-hook contact; leave
        # ring/CAS/output planes under firmware control.
        address = _phys(sensors + 8)
        value = self.uc.mem_read(address, 1)[0]
        value = value | mask if off_hook else value & ~mask
        self.uc.mem_write(address, bytes((value,)))

    def call(self, address: int, *args: int, max_insns: int = 1_000_000) -> int:
        """Run one firmware callback without disturbing the suspended task."""
        context = self.uc.context_save()
        try:
            self.uc.reg_write(UC_MIPS_REG_SP, self.layout.stack_top - 0x200)
            self.uc.reg_write(UC_MIPS_REG_RA, RETURN_VIRT)
            for register, value in zip((UC_MIPS_REG_A0, UC_MIPS_REG_A1,
                                        UC_MIPS_REG_A2, UC_MIPS_REG_A3), args):
                self.uc.reg_write(register, value)
            self.uc.emu_start(address, RETURN_VIRT, count=max_insns)
            return self.uc.reg_read(UC_MIPS_REG_V0)
        finally:
            self.uc.context_restore(context)

    def configure_audio(self, channel: int = 1, timeslot: int = 1) -> tuple[int, bytes]:
        """Apply AudioCh/AudioTS through the recovered native callbacks."""
        if not 1 <= channel <= 8 or not 1 <= timeslot <= 64:
            raise ValueError('channel must be 1..8 and timeslot must be 1..64')
        root = struct.unpack('<I', self.uc.mem_read(0x112C0, 4))[0]
        pots = struct.unpack('<I', self.uc.mem_read(_phys(root + 0xD14), 4))[0]
        if not pots:
            raise RuntimeError('POTS controller is not initialized')

        scratch = bytearray(0x40)
        scratch[0] = channel
        struct.pack_into('<I', scratch, 0x18, 0x50)  # descriptor +8
        self.uc.mem_write(MGMT_SCRATCH_PHYS, bytes(scratch))
        self.call(0x80096488, MGMT_SCRATCH_VIRT, MGMT_SCRATCH_VIRT + 0x10,
                  1, pots)
        self.call(0x80096488, MGMT_SCRATCH_VIRT, MGMT_SCRATCH_VIRT + 0x10,
                  0, pots)

        scratch = bytearray(0x40)
        scratch[(timeslot - 1) // 8] = 1 << ((timeslot - 1) % 8)
        struct.pack_into('<I', scratch, 0x18, 0x50)
        self.uc.mem_write(MGMT_SCRATCH_PHYS, bytes(scratch))
        self.call(0x80099D1C, MGMT_SCRATCH_VIRT, MGMT_SCRATCH_VIRT + 0x10,
                  1, pots)
        self.call(0x80099D1C, MGMT_SCRATCH_VIRT, MGMT_SCRATCH_VIRT + 0x10,
                  0, pots)
        selected = struct.unpack('<I', self.uc.mem_read(_phys(pots + 0x308), 4))[0]
        bitmap = bytes(self.uc.mem_read(_phys(pots + 0x30C), 8))
        return selected, bitmap

    def report(self) -> str:
        pc = self.uc.reg_read(UC_MIPS_REG_PC)
        pages = ','.join(f'0x{p:06x}' for p, _ in sorted(self.mapped))
        tail = ' '.join(f'{address:08x}' for address in self.blocks[-12:])
        insns = ' '.join(f'{address:08x}' for address in self.instructions)
        bootstrap = ' '.join(f'{address:08x}/t0={t0:08x}'
                             for address, t0 in self.bootstrap_instructions)
        fault = ('none' if self.unmapped_fault is None else
                 '/'.join(str(value) for value in self.unmapped_fault))
        return (f"pc=0x{pc:08x} self-loop="
                f"{('none' if self.self_loop is None else hex(self.self_loop))}\n"
                f"null hardware callbacks={self.null_callbacks}\n"
                f"unmapped fault={fault}\n"
                f"mapped physical pages: {pages}\nlast blocks: {tail}\n"
                f"property element: "
                f"{('unknown' if self.property_element is None else '/'.join(hex(v) for v in self.property_element))}\n"
                f"scheduler pointer: "
                f"{('unknown' if self.scheduler_pointer is None else hex(self.scheduler_pointer))}\n"
                f"property pointer: "
                f"{('unknown' if self.property_pointer is None else hex(self.property_pointer))}\n"
                f"object pointer: "
                f"{('unknown' if self.object_pointer is None else hex(self.object_pointer))}\n"
                f"memsets: "
                f"{' '.join('/'.join(hex(v) for v in item) for item in self.memsets)}\n"
                f"last memset range: "
                f"{('unknown' if self.memset_range is None else '/'.join(hex(v) for v in self.memset_range))}\n"
                f"bootstrap instructions: {bootstrap}\n"
                f"clock-read instructions: {insns}\n"
                f"clock root/object at fault: "
                f"{('unknown' if self.clock_root is None else hex(self.clock_root))}/"
                f"{('unknown' if self.clock_object is None else hex(self.clock_object))}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('image', nargs='?', type=Path,
                    default=Path('docs/firmware/build-109/te_dmlt.am'))
    ap.add_argument('--card-type', type=lambda value: int(value, 0), default=77)
    ap.add_argument('--max-insns', type=int, default=5_000_000)
    ap.add_argument('--configure-audio', action='store_true',
                    help='enable native AudioCh 1 and AudioTS 1 after boot')
    ap.add_argument('--entry-only', action='store_true',
                    help='skip the reset/bootstrap (diagnostic; allocator is uninitialized)')
    args = ap.parse_args()
    boot = AnalogMipsBoot(args.image, args.card_type)
    print(f"[analog-mips] {boot.layout.build}; entry=0x{boot.layout.entry:08x} "
          f"sp=0x{boot.layout.stack_top:08x} card-type={args.card_type}")
    try:
        boot.run(args.max_insns, reset=not args.entry_only)
    except UcError as exc:
        print(f"[analog-mips] stopped: {exc}")
    if args.configure_audio:
        selected, bitmap = boot.configure_audio()
        print(f"[analog-mips] AudioCh={selected} AudioTS={bitmap.hex()}")
    print(boot.report())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
