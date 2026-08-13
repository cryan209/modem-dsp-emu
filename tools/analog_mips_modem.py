#!/usr/bin/env python3
"""SIP-facing Analog modem with build-109 POTS supervision.

The ADSP media engine remains the recovered Analog kernel/TIKRNL stack.  The
build-109 MIPS image owns POTS allocation, audio routing, hook input, and its
indexed DSP mailbox.  This is the incremental bridge needed before replacing
the direct modem assignment with native IDI call control.
"""
from __future__ import annotations

import ctypes
from pathlib import Path

from analog_mips_boot import AnalogMipsBoot
from dial_tikrnl_drive import ADSP, Card
from eicon_dsp_extract import load_sparse, parse_combifile
from eicon_dsp_stage import required_downloads

ADSP.adsp2181_destroy.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_reset.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_pm.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_dm.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_run.argtypes = [ctypes.c_void_p, ctypes.c_int]
ADSP.adsp2181_idle.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_idle.restype = ctypes.c_int
ADSP.adsp2181_host_write.argtypes = [ctypes.c_void_p, ctypes.c_uint16,
                                     ctypes.c_uint16]
ADSP.adsp2181_host_read.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
ADSP.adsp2181_host_read.restype = ctypes.c_uint16
ADSP.adsp2181_set_irq.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]


class AnalogMipsModem:
    firmware_set = "analog109"

    def __init__(self, image: Path, law: str = "pcmu", mips_interval: int = 160,
                 modem_role: str = "answer"):
        self.card = Card()
        self.mips = AnalogMipsBoot(image, card_type=77)
        self.law = law
        self.modem_role = modem_role
        self.mips_interval = max(1, mips_interval)
        self._samples = 0
        self._selected_block: int | None = None
        self._booted = False
        self._applied_writes: dict[tuple[int, int], int] = {}
        self._analog_line = None
        self._native_downloads = None
        self._native_dspdaa_running = False
        self._dspdaa_cpu = None
        self._dspdaa_pm = None
        self._dspdaa_dm = None
        self._daa_status_published = None

    def __getattr__(self, name):
        return getattr(self.card, name)

    def _load_native_download(self, download_id: int, *, cpu=None,
                              pm=None, dm=None) -> str:
        """Apply one build-109 portable image to an ADSP core."""
        if self._native_downloads is None:
            archive = self.mips.image.parent / 'dspdload.bin'
            combi = parse_combifile(archive)
            downloads, _ = required_downloads(combi, 77)
            self._native_downloads = {item['download_id']: item
                                      for item in downloads}
        download = self._native_downloads[download_id]
        pm_image, pm_loaded = load_sparse(
            download['pm_blocks'], 0xFFFFFF, 'PM')
        dm_image, dm_loaded = load_sparse(
            download['dm_blocks'], 0xFFFF, 'DM')
        target_pm = self.card.pm if pm is None else pm
        target_dm = self.card.dm if dm is None else dm
        for address in pm_loaded:
            target_pm[address] = pm_image[address]
        for address in dm_loaded:
            target_dm[address] = dm_image[address]
        if target_pm is self.card.pm:
            self.card.pm_loaded.update(pm_loaded)
        return download['description']

    def _boot_native_dspdaa(self) -> None:
        """Run Analog kernel 0x000d and register IDLE.ANA task 0x0063."""
        self._dspdaa_cpu = ADSP.adsp2181_create()
        ADSP.adsp2181_reset(self._dspdaa_cpu)
        self._dspdaa_pm = ADSP.adsp2181_pm(self._dspdaa_cpu)
        self._dspdaa_dm = ADSP.adsp2181_dm(self._dspdaa_cpu)
        kernel = self._load_native_download(
            0x000D, cpu=self._dspdaa_cpu,
            pm=self._dspdaa_pm, dm=self._dspdaa_dm)
        ADSP.adsp2181_run(self._dspdaa_cpu, 50_000)
        if not ADSP.adsp2181_idle(self._dspdaa_cpu):
            raise RuntimeError('native Analog kernel did not reach IDLE')
        idle = self._load_native_download(
            0x0063, cpu=self._dspdaa_cpu,
            pm=self._dspdaa_pm, dm=self._dspdaa_dm)
        ADSP.adsp2181_call(self._dspdaa_cpu, 0x0900, 0x02A8)
        ADSP.adsp2181_run(self._dspdaa_cpu, 50_000)
        if not ADSP.adsp2181_idle(self._dspdaa_cpu):
            raise RuntimeError('IDLE.ANA did not return to Analog kernel')
        self._native_dspdaa_running = True
        print(f'[analog-mips] native DSPDAA running: {kernel}; {idle}')

    def boot(self) -> None:
        if self._booted:
            return
        # Keep DSPDAA supervision on its own native core while the existing
        # media core remains available for TIKRNL/overlays. This mirrors the
        # firmware's physical-core ownership and avoids replacing DSPDAA at
        # modem assignment.
        self.card.boot()
        self._boot_native_dspdaa()
        # Let board discovery enumerate all four physical blocks against the
        # register model. Attach only the selected channel after initialization;
        # forwarding discovery writes into an already-running direct ADSP would
        # reset/corrupt that core before POTS has selected a channel.
        self.mips.run(5_000_000)
        selected, bitmap = self.mips.configure_audio(channel=1, timeslot=1)
        if self.mips.hw_reads:
            self._selected_block = self.mips.hw_reads.most_common(1)[0][0][0]
        # Discovery writes configured the synthetic board model, not this
        # already-running ADSP. Only writes issued after attachment belong on
        # the live mailbox.
        self._applied_writes = dict(self.mips.hw_writes)
        native_dm = self.mips.dsp_dm.get(self._selected_block or 0)
        native_pm = self.mips.dsp_pm.get(self._selected_block or 0)
        if (not self._native_dspdaa_running and native_dm is not None
                and native_pm is not None and any(native_pm)):
            # Board initialization has executed the native segmented loader
            # against a shadow core. Publish that exact kernel image to the
            # selected emulator before modem assignment; subsequent mailbox
            # traffic is synchronized incrementally.
            # This is a replacement image, not an overlay: zero words matter.
            # Reset and execute the firmware-loaded kernel before allowing any
            # DSPDAA mailbox traffic. Previously we copied only nonzero words
            # over the already-running direct modem task, so no native kernel
            # ever answered line-supervision commands.
            for address, value in enumerate(native_dm):
                self.card.dm[address] = value
            for address, value in enumerate(native_pm):
                self.card.pm[address] = value
            self.card.pm_loaded = {address for address, value in
                                   enumerate(native_pm) if value}
            ADSP.adsp2181_reset(self.card.cpu)
            ADSP.adsp2181_run(self.card.cpu, 20_000)
            print(f"[analog-mips] attached native DSP kernel "
                  f"({sum(bool(v) for v in native_pm)} PM, "
                  f"{sum(bool(v) for v in native_dm)} DM words, "
                  f"idle={ADSP.adsp2181_idle(self.card.cpu)})")
        if selected != 1 or bitmap != b"\x01" + bytes(7):
            raise RuntimeError(
                f"Analog audio route rejected: channel={selected} bitmap={bitmap.hex()}")
        self._booted = True
        print(f"[analog-mips] POTS active; DSP block=0x{self._selected_block or 0:08x} "
              f"AudioCh={selected} AudioTS={bitmap.hex()}")

    def _publish_daa_line_status(self) -> None:
        """Drive the stable Si301x status words sampled by build-109.

        The native Analog kernel owns command timing and SPORT1. The missing
        silicon below it contributes only physical measurements: frame detect,
        loop-current sense and signed line voltage. These are the same fields
        recovered in courier-emu, translated onto build-109's stable DSPDAA
        status words rather than fabricated in MIPS RAM.
        """
        if not self._native_dspdaa_running or self._analog_line is None:
            return
        status = self._analog_line.daa_line_status & 0xFFFF
        voltage = self._analog_line.line_voltage_sense & 0xFFFF
        current = self._analog_line.loop_current_sense & 0xFFFF
        published = (status, voltage, current)
        if published == self._daa_status_published:
            return
        # 2e5e is the line-status word reached after SPORT1 service; 2e5f and
        # 2e60 are the adjacent voltage/current values polled by the MIPS DAA
        # driver. Preserve the kernel's active flag in bit 15.
        self._dspdaa_dm[0x2E5E] = 0x8000 | status
        self._dspdaa_dm[0x2E5F] = voltage
        self._dspdaa_dm[0x2E60] = current
        self._daa_status_published = published

    def _sync_mailbox_from_adsp(self) -> None:
        if self._selected_block is None:
            return
        self._publish_daa_line_status()
        cpu = (self._dspdaa_cpu if self._native_dspdaa_running
               else self.card.cpu)
        registers = {register for base, register in self.mips.hw_reads
                     if base == self._selected_block}
        # DSPDAA indications are intentionally transient and may be published
        # before MIPS has performed its first read of that mailbox word.
        registers.update((0x4000, 0x4009, 0x400A, 0x400B))
        direct_dm = self.mips.dsp_dm.setdefault(
            self._selected_block, [0] * 0x4000)
        # These words are also consumed through the direct IDMA MMIO path,
        # bypassing 0x80104418. Mirror the live core into that shadow before
        # Unicorn runs or MIPS will keep seeing its stale loader-time image.
        direct_addresses = (0x0000, 0x0009, 0x000A, 0x000B,
                            0x2E02, 0x2E19, 0x2E50, 0x2E5E, 0x2E5F, 0x2E60)
        for address in direct_addresses:
            direct_dm[address] = int(self._dspdaa_dm[address])
        for register in registers:
            self.mips.hw_registers[(self._selected_block, register)] = int(
                ADSP.adsp2181_host_read(cpu, register))

    def _run_dspdaa_foreground(self) -> None:
        """Wake the Analog kernel after an indexed host/DAA event."""
        if not self._native_dspdaa_running:
            return
        # PM 0x02a1 is the resident kernel command/foreground dispatcher.
        # Hardware wakes this path after an IDMA command or DAA edge; IDLE.ANA
        # itself does not spin while the core is at PM 0x02a6.
        ADSP.adsp2181_call(self._dspdaa_cpu, 0x02A1, 0x02A8)
        for _ in range(64):
            ADSP.adsp2181_run(self._dspdaa_cpu, 2_000)
            if ADSP.adsp2181_idle(self._dspdaa_cpu):
                return
        raise RuntimeError('DSPDAA foreground did not return to IDLE')

    def _sync_mailbox_to_adsp(self) -> None:
        if self._selected_block is None:
            return
        wake = False
        for key, count in tuple(self.mips.hw_writes.items()):
            if key[0] != self._selected_block or self._applied_writes.get(key) == count:
                continue
            ADSP.adsp2181_host_write(
                self._dspdaa_cpu if self._native_dspdaa_running
                else self.card.cpu, key[1], self.mips.hw_registers.get(key, 0))
            self._applied_writes[key] = count
            wake = True
        if wake:
            self._run_dspdaa_foreground()

    def _adopt_native_selected_block(self) -> None:
        if not self.mips.native_download_blocks:
            return
        selected = self.mips.native_download_blocks[-1]
        if not selected or selected == self._selected_block:
            return
        old = self._selected_block
        self._selected_block = selected
        # Do not replay discovery writes from another physical core. Runtime
        # writes made after dsp_assign will be committed by the normal sync.
        self._applied_writes = dict(self.mips.hw_writes)
        print(f"[analog-mips] native dsp_assign selected 0x{selected:08x} "
              f"(discovery heuristic was 0x{old or 0:08x})")

    def _step_mips(self, instructions: int) -> None:
        # Do not call the ADSP shared library recursively from a Unicorn hook:
        # both emulators use native callbacks and that re-entry crashes ctypes
        # on macOS. Snapshot, run MIPS against the shadow mailbox, then commit.
        self._sync_mailbox_from_adsp()
        self.mips.step(instructions)
        self._adopt_native_selected_block()
        for request, download in self.mips.native_download_requests:
            if (download == 0x0258
                    and request not in self.mips._native_download_completions):
                result = self.mips.complete_native_download(request)
                print(f"[analog-mips] native download callback 0x{download:04x} "
                      f"request=0x{request:08x} result={result}")
        # Discovery writes were baselined at attachment. Writes after that are
        # replies to the live task's mailbox and must be visible before its
        # next SPORT sample.
        self._sync_mailbox_to_adsp()

    def configure_modem(self, role: str, law: str = "pcmu") -> None:
        # Native POTS owns the physical route/hook. Modem CAI assignment still
        # uses the recovered direct ADSP database until Analog IDI is wired.
        # ATD is host-side call control. Its modem assignment starts the data
        # pump in the calling DIAL page; DIAL owns tone/dial progress and must
        # request V.8 itself when the line is ready. Loading V8.ANA here skips
        # precisely the state that a real tty-originated call is meant to run.
        self.card.configure_modem(self.modem_role, law)

    def attach_analog_line(self, line) -> None:
        self._analog_line = line
        self.mips.attach_analog_line(line)

    def begin_dial(self, number: str) -> bool:
        """Send the tty ATD number through build-109's native IDI queue."""
        if not self.mips.line_in_service(channel=1):
            print("[analog-mips] native CALL_REQ rejected: no exchange battery")
            return False
        accepted = self.mips.request_outgoing_call(number)
        if accepted:
            print(f"[analog-mips] native CALL_REQ queued for {number}")
        else:
            print("[analog-mips] native signalling ASSIGN failed")
        return accepted

    def set_line_hook(self, off_hook: bool) -> None:
        if self._analog_line is not None:
            self._analog_line.set_hook(off_hook)
        self.mips.set_hook_input(off_hook, channel=1)
        # Give the native state machine a bounded opportunity to consume the
        # edge without charging every 8 kHz sample for a MIPS pass.
        self._step_mips(100_000)

    def line_rx_word(self, code: int, linear: int) -> int:
        return self.card.line_rx_word(code, linear)

    def frame_fast(self, word: int, sample_index: int) -> int:
        # SPORT1 codec clocking is owned by the active physical core. The
        # supervisory DSPDAA core is command-driven here; asserting its aliased
        # IRQ0 without a complete serial frame nests native interrupt returns.
        value = self.card.frame_fast(word, sample_index)
        self._samples += 1
        # TIKRNL publishes short-lived command pointers in DM(0/9/a/b). A
        # 20-ms supervisory poll misses them because the DSP clears the words
        # on a later sample. Service an active mailbox immediately; retain the
        # bounded interval for ordinary POTS timers.
        mailbox_active = any(self.card.dm[address]
                             for address in (0x0000, 0x0009, 0x000A, 0x000B))
        if mailbox_active or self._samples % self.mips_interval == 0:
            self._step_mips(50_000)
        return value


def create_analog_mips_modem(image: Path, law: str = "pcmu",
                             mips_interval: int = 160,
                             modem_role: str = "answer") -> AnalogMipsModem:
    return AnalogMipsModem(image=image, law=law, mips_interval=mips_interval,
                           modem_role=modem_role)
