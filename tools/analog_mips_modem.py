#!/usr/bin/env python3
"""SIP-facing Analog modem with build-109 POTS supervision.

The ADSP media engine remains the recovered Analog kernel/TIKRNL stack.  The
build-109 MIPS image owns POTS allocation, audio routing, hook input, and its
indexed DSP mailbox.  This is the incremental bridge needed before replacing
the direct modem assignment with native IDI call control.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path

from analog_mips_boot import AnalogMipsBoot
from analog_kernel_dispatch import AnalogKernelModem
from dial_tikrnl_drive import ADSP
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
ADSP.adsp2181_sport1_frame.argtypes = [ctypes.c_void_p, ctypes.c_uint16,
                                       ctypes.c_int]
ADSP.adsp2181_sport1_frame.restype = ctypes.c_uint32
ADSP_RX_CB = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_int)
ADSP_TX_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int,
                             ctypes.c_int32)
ADSP_TIMER_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int)
ADSP.adsp2181_set_callbacks.argtypes = [ctypes.c_void_p, ADSP_RX_CB,
                                        ADSP_TX_CB, ADSP_TIMER_CB]


class AnalogMipsModem:
    firmware_set = "analog109"

    def __init__(self, image: Path, law: str = "pcmu", mips_interval: int = 160,
                 modem_role: str = "answer"):
        # Native MIPS owns POTS/DSPDAA, but the modem media core is still the
        # Analog SPORT1 kernel path.  A raw Card here silently selects the
        # direct 8-kHz path; that path cannot run analog109 V.8/V.90 because
        # the firmware requests the 9600-Hz codec rate.  Keep the native
        # supervision core separate while reusing the proven 9600->8000
        # kernel-dispatch media boundary.
        self.card = AnalogKernelModem(modem_role=modem_role, law=law,
                                       codec_rate=9600)
        self.mips = AnalogMipsBoot(image, card_type=77)
        self.law = law
        self.modem_role = modem_role
        self.mips_interval = max(1, mips_interval)
        self.mips_step_budget = max(1, int(os.environ.get(
            'EICON_MIPS_STEP_BUDGET', '50000'), 0))
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
        self._dspdaa_callbacks = None
        self._dspdaa_rx_word = 0
        self._dspdaa_tx_word = 0
        self._dspdaa_tx_count = 0
        self._last_mailbox_signature: tuple[int, ...] | None = None
        # The native MIPS image writes the assigned DSP through the IDMA
        # window.  Keep this bridge opt-in: it is useful for proving mailbox
        # ownership, but a stale loader-time shadow must never replace the
        # recovered media core's own TX source during normal runs.
        self._bridge_native_v90a_tx = (
            os.environ.get('EICON_NATIVE_BRIDGE_V90A_TX', '0') != '0')
        self._trace_native_v90a_tx = (
            os.environ.get('EICON_TRACE_NATIVE_V90A_TX', '0') != '0')
        self._native_v90a_tx_last: tuple[int, int] | None = None
        # During modem assignment the selected DSP mailbox belongs to the
        # media core.  The native DAA core is separate and must not be the
        # only recipient of post-assignment host writes.  Keep mirroring
        # opt-in until the live pair qualifies the ownership model.
        self._mirror_media_mailbox = (
            os.environ.get('EICON_NATIVE_MIRROR_MEDIA_MAILBOX', '0') != '0')
        self._trace_native_hw_writes = (
            os.environ.get('EICON_TRACE_NATIVE_HW_WRITES', '0') != '0')
        self._trace_native_media_shadow = (
            os.environ.get('EICON_TRACE_NATIVE_MEDIA_SHADOW', '0') != '0')
        self._replace_media_from_native = (
            os.environ.get('EICON_NATIVE_REPLACE_MEDIA', '0') != '0')
        self._replay_native_media_writes = (
            os.environ.get('EICON_NATIVE_REPLAY_MEDIA_WRITES', '0') != '0')
        self._replay_native_media_write_log = (
            os.environ.get('EICON_NATIVE_REPLAY_MEDIA_WRITE_LOG', '0') != '0')
        self._native_media_replaced = False
        # Diagnostic only: the native DSPDAA core is normally clocked before
        # the recovered modem media core.  Bypassing that separate SPORT
        # exchange lets the live-pair harness distinguish a DAA-core timing
        # interaction from the Analog modem DSP itself.
        self._clock_native_dspdaa = (
            os.environ.get('EICON_NATIVE_SKIP_DSPDAA_CLOCK', '0') == '0')
        # Diagnostic only: the native Analog/DSPDAA core is normally clocked
        # for supervision while the recovered modem core owns the media
        # sample.  Routing its SPORT1 TX word through the line tests the
        # alternate 2185 codec boundary without changing the qualified path.
        self._use_native_dspdaa_tx = (
            os.environ.get('EICON_NATIVE_USE_DSPDAA_TX', '0') != '0')

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

        def sport_rx(_cpu, port):
            return self._dspdaa_rx_word if port == 1 else 0

        def sport_tx(_cpu, port, value):
            if port == 1:
                self._dspdaa_tx_word = value & 0xFFFF
                self._dspdaa_tx_count += 1

        self._dspdaa_callbacks = (
            ADSP_RX_CB(sport_rx), ADSP_TX_CB(sport_tx),
            ADSP_TIMER_CB(lambda _cpu, _enabled: None))
        ADSP.adsp2181_set_callbacks(self._dspdaa_cpu,
                                    *self._dspdaa_callbacks)
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
        if self._trace_native_media_shadow:
            print('[analog-mips] selected media shadow: '
                  f'block=0x{self._selected_block or 0:08x} '
                  f'PM={sum(bool(v) for v in native_pm or ())} '
                  f'DM={sum(bool(v) for v in native_dm or ())} '
                  f'requests=' + ','.join(
                      f'0x{download:04x}'
                      for _request, download in self.mips.native_download_requests))
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

    def _clock_dspdaa_sport1(self, receive_word: int = 0) -> int:
        """Exchange one simultaneous 16-bit Si3056 SPORT1 audio frame.

        In ASIC-slave mode the Si3056 frame is 128 SCLKs, but only one 16-bit
        RCVO/RCVI word is active in each direction. Control is a later frame
        selected by the DAA's framing state; it must not be invented by
        directly changing DSP kernel status words.
        """
        if not self._native_dspdaa_running:
            return 0
        # receive_word is already the codec-side signed-linear sample. The SIP
        # boundary owns the two-wire gain/echo pass, so do not apply it twice
        # merely because DSPDAA and TIKRNL are represented by separate cores.
        self._dspdaa_rx_word = receive_word & 0xFFFF
        # SPORT1's shared RX/TX interrupt is wired to the ADSP IRQ1 alias in
        # this board image. The native ISR reads RX1 and writes TX1 before
        # returning to IDLE. Keep write detection in C because an immediate-DM
        # `TX1 = DM(...)` instruction is an external transfer too.
        result = ADSP.adsp2181_sport1_frame(
            self._dspdaa_cpu, self._dspdaa_rx_word, 500)
        transmitted = result & 0xFFFF if result & 0x10000 else 0
        return transmitted - 0x10000 if transmitted & 0x8000 else transmitted

    def _sync_mailbox_from_adsp(self) -> None:
        if self._selected_block is None:
            return
        cpu = (self.card.cpu if self._native_media_replaced else
               (self._dspdaa_cpu if self._native_dspdaa_running
                else self.card.cpu))
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
        source_dm = (self.card.dm if self._native_media_replaced
                     else self._dspdaa_dm)
        for address in direct_addresses:
            direct_dm[address] = int(source_dm[address])
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
            value = self.mips.hw_registers.get(key, 0)
            target = (self.card.cpu if self._native_media_replaced else
                      (self._dspdaa_cpu if self._native_dspdaa_running
                       else self.card.cpu))
            if self._trace_native_hw_writes:
                print('[analog-mips] host write '
                      f'base=0x{key[0]:08x} reg=0x{key[1]:04x} '
                      f'value=0x{value:04x} media=0x{self.card.resident:04x}')
            ADSP.adsp2181_host_write(target, key[1], value)
            if self._mirror_media_mailbox and target != self.card.cpu:
                ADSP.adsp2181_host_write(self.card.cpu, key[1], value)
            self._applied_writes[key] = count
            wake = True
        if wake:
            if self._native_media_replaced:
                ADSP.adsp2181_call(self.card.cpu, 0x029E, 0x02A5)
                for _ in range(64):
                    ADSP.adsp2181_run(self.card.cpu, 2_000)
                    if ADSP.adsp2181_idle(self.card.cpu):
                        break
                else:
                    raise RuntimeError(
                        'native ANA media foreground did not return')
            else:
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
        if self._trace_native_media_shadow:
            shadow_pm = self.mips.dsp_pm.get(selected)
            shadow_dm = self.mips.dsp_dm.get(selected)
            print('[analog-mips] adopted media shadow: '
                  f'block=0x{selected:08x} '
                  f'PM={sum(bool(v) for v in shadow_pm or ())} '
                  f'DM={sum(bool(v) for v in shadow_dm or ())} '
                  f'requests=' + ','.join(
                      f'0x{download:04x}'
                      for _request, download in self.mips.native_download_requests))
        print(f"[analog-mips] native dsp_assign selected 0x{selected:08x} "
              f"(discovery heuristic was 0x{old or 0:08x})")

    def _replace_media_core_from_native(self) -> None:
        """Run the native ANA image on the existing SPORT1 core (A/B only)."""
        if (not self._replace_media_from_native or self._native_media_replaced
                or self._selected_block is None):
            return
        cpu = self.card.cpu
        pm = self.card.pm
        dm = self.card.dm
        ADSP.adsp2181_reset(cpu)
        ADSP.adsp2181_set_callbacks(cpu, *self.card.driver._cbs)
        # Follow the same resident lifecycle as the proven DSPDAA core. The
        # selected shadow is diagnostic evidence; the archive is the complete
        # portable image needed to execute the resident kernel safely.
        self._load_native_download(0x000D, pm=pm, dm=dm)
        ADSP.adsp2181_run(cpu, 50_000)
        if not ADSP.adsp2181_idle(cpu):
            raise RuntimeError('native ANA kernel did not reach IDLE')
        # Establish the Analog kernel's SPORT command ring first, then let
        # the foreground dispatch the task entry.  Calling 0x0679 directly
        # skips the registration/continuation vectors and leaves TrnProgress
        # at zero even though all code words are present.
        ADSP.adsp2181_set_callbacks(cpu, *self.card.driver._cbs)
        for _ in range(32):
            self.card.driver.frame()
            if dm[0x2E7B]:
                break
        else:
            raise RuntimeError('native ANA command ring did not initialise')
        self._load_native_download(0x0258, pm=pm, dm=dm)
        if not self.card.driver.push(0x0679):
            raise RuntimeError('native ANA task command was not accepted')
        empty_slot = self.card.driver._call_word(0x029E)
        for _ in range(32):
            self.card.driver.frame()
            if dm[0x31BA] and pm[0x02B6] != empty_slot:
                break
        else:
            raise RuntimeError('native ANA TIKRNL did not register')
        if self._replay_native_media_write_log:
            # Preserve the firmware's indexed-write order.  Replaying only
            # the final register snapshot after overlays are resident can
            # execute a command at the wrong lifecycle point; the native DSP
            # sees these writes while 0x0258 is resident, before the overlay
            # downloads below, just as the selected hardware block did.
            events = [
                (register, value)
                for block, register, value in self.mips.hw_write_log
                if block == self._selected_block
            ]
            for register, value in events:
                ADSP.adsp2181_host_write(cpu, register, value)
                ADSP.adsp2181_run(cpu, 2_000)
            print('[analog-mips] replayed native selected-block write log '
                  f'({len(events)} writes)')
        for download in (0x026D, 0x025C, 0x0262):
            self._load_native_download(download, pm=pm, dm=dm)
        self.card.card.resident = 0x0262
        self.card.resident = 0x0262
        self.card.configure_modem(self.modem_role, self.law)
        if self._replay_native_media_writes:
            # The normal bridge baselines discovery/loader writes so they do
            # not corrupt the recovered media owner.  A native replacement
            # core needs the selected block's final register image, however:
            # portable PM/DM downloads alone do not restore SPORT/DAA setup.
            # Replay the captured snapshot only for this opt-in native-core
            # experiment; runtime writes continue through _sync_mailbox_to_adsp.
            registers = sorted(
                (register, value)
                for (block, register), value in self.mips.hw_registers.items()
                if block == self._selected_block)
            for register, value in registers:
                ADSP.adsp2181_host_write(cpu, register, value)
            print('[analog-mips] replayed native selected-block register '
                  f'snapshot ({len(registers)} registers)')
        self._native_media_replaced = True
        print('[analog-mips] replaced SPORT1 media with native ANA image '
              f'after dsp_assign block=0x{self._selected_block:08x}')

    def _step_mips(self, instructions: int) -> None:
        # Do not call the ADSP shared library recursively from a Unicorn hook:
        # both emulators use native callbacks and that re-entry crashes ctypes
        # on macOS. Snapshot, run MIPS against the shadow mailbox, then commit.
        self._sync_mailbox_from_adsp()
        shadow = (self.mips.dsp_dm.get(self._selected_block)
                  if self._selected_block is not None else None)
        before_tx = (tuple(shadow[address] for address in (0x3F05, 0x3FAD))
                     if shadow is not None else None)
        self.mips.step(instructions)
        self._adopt_native_selected_block()
        shadow = (self.mips.dsp_dm.get(self._selected_block)
                  if self._selected_block is not None else None)
        after_tx = (tuple(shadow[address] for address in (0x3F05, 0x3FAD))
                    if shadow is not None else None)
        if after_tx is not None and after_tx != before_tx:
            if self._trace_native_v90a_tx:
                print('[analog-mips] native DSP TX mailbox changed: '
                      f'DM(3f05)=0x{after_tx[0]:04x} '
                      f'DM(3fad)=0x{after_tx[1]:04x}')
            if (self._bridge_native_v90a_tx and self.card.resident == 0x026B
                    and self.card.dm[0x3FAD] & 0x8000):
                # The APCM page consumes TXD0 only.  Copy a newly-produced
                # native word before the next recovered SPORT frame; do not
                # copy the request bit itself because it belongs to the
                # recovered page's local request/ack cadence.
                self.card.dm[0x3F05] = after_tx[0]
        for request, download in self.mips.native_download_requests:
            if (download == 0x0258
                    and request not in self.mips._native_download_completions):
                result = self.mips.complete_native_download(request)
                # The MIPS callback only completes the protocol-side
                # assignment.  The host's companion operation is to transfer
                # the requested portable image into the selected DSP's IDMA
                # space.  AnalogMipsBoot captures the kernel/IDLE transfer
                # through MMIO, but the 0x0258 request is asynchronous and
                # previously stopped after the acknowledgement, leaving the
                # selected shadow with only the 426/102-word bootstrap.
                # Populate the shadow with the exact ANA variant selected by
                # card type 77.  This does not change the recovered media
                # owner; it makes the native selected-core experiment and
                # its diagnostics represent the hardware download.
                shadow_pm = self.mips.dsp_pm.setdefault(
                    self._selected_block, [0] * 0x4000)
                shadow_dm = self.mips.dsp_dm.setdefault(
                    self._selected_block, [0] * 0x4000)
                description = self._load_native_download(
                    download, pm=shadow_pm, dm=shadow_dm)
                if self._trace_native_media_shadow:
                    print('[analog-mips] staged native media download '
                          f'0x{download:04x}: {description}; '
                          f'PM={sum(bool(v) for v in shadow_pm)} '
                          f'DM={sum(bool(v) for v in shadow_dm)}')
                self._replace_media_core_from_native()
                print(f"[analog-mips] native download callback 0x{download:04x} "
                      f"request=0x{request:08x} result={result}")
        # Discovery writes were baselined at attachment. Writes after that are
        # replies to the live task's mailbox and must be visible before its
        # next SPORT sample.
        self._sync_mailbox_to_adsp()

    def configure_modem(self, role: str, law: str = "pcmu") -> None:
        # Native POTS owns the physical route/hook. The native IDI call path
        # now carries the selected CAI; this recovered card remains the media
        # owner for the incremental bridge.
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
        # Clock the physical Si3056-facing core once for every 8 kHz codec
        # frame. The separately recovered modem core still owns media output
        # until native DSP assignment joins their routing.
        native_tx = None
        if getattr(self, '_clock_native_dspdaa', True):
            native_tx = self._clock_dspdaa_sport1(word)
        value = self.card.frame_fast(word, sample_index)
        if self._use_native_dspdaa_tx and native_tx is not None:
            value = native_tx
        self._samples += 1
        # TIKRNL publishes short-lived command pointers in DM(0/9/a/b). A
        # 20-ms supervisory poll misses them because the DSP clears the words
        # on a later sample. Service an active mailbox immediately; retain the
        # bounded interval for ordinary POTS timers.
        # These are command/mailbox words, not level-triggered interrupts.
        # A completed command may leave its descriptor pointer non-zero, so
        # testing `any(word)` here runs a 50k-instruction MIPS pass on every
        # audio frame.  That makes the calling tower spend seconds per 20 ms
        # tick and prevents V.8 from advancing.  Poll on a mailbox edge, then
        # use the normal periodic supervisor pass for timers and line state.
        mailbox_signature = tuple(self.card.dm[address]
                                  for address in (0x0000, 0x0009, 0x000A, 0x000B))
        mailbox_edge = (mailbox_signature != self._last_mailbox_signature)
        self._last_mailbox_signature = mailbox_signature
        if mailbox_edge or self._samples % self.mips_interval == 0:
            self._step_mips(getattr(self, 'mips_step_budget', 50000))
        return value


def create_analog_mips_modem(image: Path, law: str = "pcmu",
                             mips_interval: int = 160,
                             modem_role: str = "answer") -> AnalogMipsModem:
    return AnalogMipsModem(image=image, law=law, mips_interval=mips_interval,
                           modem_role=modem_role)
