#!/usr/bin/env python3
"""Experimental BRI 117-926 caller replay through the kernel's SPORT1 ISR.

This models one task on the second channel, four 16-bit SPORT1 words per
8 kHz bearer frame, and host download completions. It does not model the
complete IOM control stream, IDI assignment or RX2400 publication. The
BriKernelModem adapter supplies live duplex audio to the SIP endpoint.
Use a separate process: the shared Card harness uses process-wide firmware
selection. It forces no DSP state transitions; Card's existing host mailbox
and V.90 delay-line adaptations still apply.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

import dial_tikrnl_drive as drive
from analog_kernel_dispatch import ADSP, RX_CB, TX_CB, TIM_CB
from eicon_live_caller_replay import compare_dwells, hardware_events

ROOT = Path('artifacts/eicon-dsp/build-117-926-bri')
KERNEL = ROOT / '0006-diva-server-bri-2m-kernel'


class BriCallerReplay:
    def __init__(self, law, modem_role='calling'):
        if law not in ('pcma', 'pcmu') or modem_role not in ('calling', 'answer'):
            raise ValueError('BRI replay requires a G.711 law and calling/answer role')
        drive.select_firmware_set('pri117')  # shared task ABI, not the kernel
        drive.MODEM_V8_SETUP = 0x8000 if modem_role == 'calling' else 0x6000
        drive.OVERLAYS = str(ROOT / 'overlays')
        previous = os.environ.get('EICON_ADSP_SPORT')
        os.environ['EICON_ADSP_SPORT'] = '1'  # RX0/TX0 hardware companding
        try:
            self.card = drive.Card(serve=False)
        finally:
            if previous is None:
                del os.environ['EICON_ADSP_SPORT']
            else:
                os.environ['EICON_ADSP_SPORT'] = previous
        self.law = law
        self.modem_role = modem_role
        self.tx_code = 0xD5 if law == 'pcma' else 0xFF
        self.silence = 0xD5 if law == 'pcma' else 0xFF
        self.word = self.silence * 0x101
        self.callbacks = (RX_CB(lambda cpu, port: self.word),
                          TX_CB(lambda *args: None), TIM_CB(lambda *args: None))

    def frame(self, code):
        # First channel idle, selected second channel in the low octet.
        # Repeated subwords are a bearer fixture, not full IOM signalling.
        self.word = (self.silence << 8) | code
        for _ in range(4):
            main_word = self.card.dm[0x2DF3] == 0
            result = ADSP.adsp2181_sport1_frame(self.card.cpu, self.word, 300000)
            if main_word:
                self.tx_code = result & 0xFF
            if not ADSP.adsp2181_idle(self.card.cpu):
                raise RuntimeError('BRI SPORT1 did not return to IDLE')

    def boot(self):
        card = self.card
        card._download(str(KERNEL))
        ADSP.adsp2181_run(card.cpu, 5000)
        if not ADSP.adsp2181_idle(card.cpu):
            raise RuntimeError('BRI kernel did not reach IDLE')
        ADSP.adsp2181_set_callbacks(card.cpu, *self.callbacks)
        for _ in range(4):
            self.frame(self.silence)
        if card.dm[0x2F09] != 0x2EE1:
            raise RuntimeError('unexpected BRI channel-two command descriptor')
        card._download(drive.TIKRNL)
        descriptor = card.dm[0x2F09]
        if card.dm[descriptor + 3]:
            raise RuntimeError('BRI command ring is busy')
        card.dm[card.dm[descriptor + 4]] = drive.TASK_ENTRY
        card.dm[descriptor + 3] = 2
        for _ in range(8):
            self.frame(self.silence)
        if card.pm[0x02B0] != 0x1C6FCF or card.pm[0x0072] != 0x1C8F6F:
            raise RuntimeError('BRI task failed to register its continuations')
        for base in (drive.V_OWN_ID, drive.FSK_OWN_ID):
            card._download(card.overlays[base][0])
        if card.download_overlay(drive.DIAL_ID) is None:
            raise RuntimeError('missing DIAL overlay')
        card.configure_modem(self.modem_role, self.law)
        # BRI descriptor's SPORT0 control: dtype 3=A-law, 2=mu-law.
        # 3c07 is uncompressed; it does not expand an A-law RX code.
        card.dm[0x2F03] = 0x3C37 if self.law == 'pcma' else 0x3C27

    def service(self, index):
        card, dm = self.card, self.card.dm
        descriptor = dm[0x2F0A]
        bits = dm[descriptor + 9] if descriptor else 0
        if bits:
            dm[descriptor + 9] = 0
        wanted = dm[0x31AA]
        if ((bits & 2 or dm[0x3FC1] & 0x100)
                and wanted and wanted != card.resident):
            if card.download_overlay(wanted) is None:
                raise RuntimeError(f'missing overlay {wanted:04x}')
            card.switches.append((index, dm[0x3FB0], wanted))
            bits |= 2
        for bit in range(16):
            if bits & (1 << bit):
                entry = dm[0x31BA + bit]
                if entry:
                    ADSP.adsp2181_call(card.cpu, entry, 0x028B)
                    ADSP.adsp2181_run(card.cpu, 1000000)
                    if not ADSP.adsp2181_idle(card.cpu):
                        raise RuntimeError('BRI host completion did not yield')
        # Card.download_overlay holds the shared V.90D bulk worker and
        # delegates its delay-line ABI to the host. The SPORT1 path must
        # publish that ABI at the same frame boundary as Card.frame_fast.
        card._service_v90d_bulk()


class BriKernelModem:
    """Duplex SIP adapter for the matching BRI kernel, without MIPS setup synthesis."""
    firmware_set = 'bri117'
    mailbox_per_codec = False

    def __init__(self, modem_role='answer', law='pcmu'):
        self.driver = BriCallerReplay(law, modem_role)
        self.card = self.driver.card

    def __getattr__(self, name):
        return getattr(self.card, name)

    def boot(self):
        self.driver.boot()

    def configure_modem(self, role, law):
        if role != self.driver.modem_role or law != self.driver.law:
            raise ValueError('BRI role and law must match construction')

    def line_rx_word(self, code, linear):
        return code

    def frame_fast(self, code, index=0):
        self.driver.frame(code)
        self.driver.service(index)
        if self.driver.law == 'pcmu' and self.driver.tx_code == 0x7F:
            # SPORT has already companded this sample. Both PCMU zero
            # octets expand to integer zero, but a subsequent RTP encoding
            # must retain the firmware's negative-zero octet. A negative
            # PCM16 zero-bin value encodes to 7f with the resident encoder.
            return -1
        word = drive.sport_rx_word(self.driver.tx_code, self.driver.law)
        value = word - 0x10000 if word & 0x8000 else word
        return value * (8 if self.driver.law == 'pcma' else 4)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--law', choices=('pcma', 'pcmu'), required=True)
    parser.add_argument('--hardware-trace', type=Path)
    parser.add_argument('--hardware-task', default='P6,2')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    data = args.capture.read_bytes()
    if not data:
        parser.error('empty capture')
    hardware = (hardware_events(args.hardware_trace, args.hardware_task)
                if args.hardware_trace else [])
    replay = BriCallerReplay(args.law)
    replay.boot()
    events = []
    previous = None
    for index, code in enumerate(data):
        replay.frame(code)
        replay.service(index)
        dm = replay.card.dm
        state = (replay.card.resident, dm[0x3FC2])
        if state != previous:
            event = dict(sample=index, seconds=index / 8000,
                         overlay=state[0], trn=state[1],
                         speed_tx=int(dm[0x3F61]), speed=int(dm[0x3F62]),
                         rstatus_ch=int(dm[0x3FC0]), rstatus=int(dm[0x3FC1]))
            events.append(event)
            print(f'{index / 8000:8.4f} overlay={state[0]:04x} '
                  f'trn={state[1]:04x}', flush=True)
            previous = state
    result = dict(harness='bri117-experimental', law=args.law,
                  samples=len(data), sha256=hashlib.sha256(data).hexdigest(),
                  bearer_rate=8000, sport1_words_per_frame=4, events=events,
                  reached_data=any(e['trn'] == 0xD0 for e in events),
                  hardware_task=args.hardware_task, hardware_events=hardware,
                  dwell_comparison=compare_dwells(hardware, events))
    args.out.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
