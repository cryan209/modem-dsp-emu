#!/usr/bin/env python3
"""Replay BRI caller RX audio through the Analog harness (a cross-family diagnostic).

The input is raw 8 kHz G.711 from mlog_audio_split.py. Select the actual
capture law explicitly; the splitter's historical .ulaw suffix is not proof
of its encoding. State events use
bearer sample indices. No state is forced. --apcm-native-rate is an explicit
mixed-firmware clock experiment, not a supported BRI machine model.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

import dial_tikrnl_drive as drive
from analog_kernel_dispatch import AnalogKernelModem, decode_mulaw


def hardware_events(path, task):
    pattern = re.compile(r'(\d+):(\d+):(\d+) - \[' + re.escape(task)
                         + r'\] TRAINPROGRESS ([0-9a-fA-F]+)')
    events = []
    for line in path.read_text(errors='replace').splitlines():
        match = pattern.search(line)
        if match:
            major, seconds, milliseconds, state = match.groups()
            events.append(dict(seconds=int(major) * 3600 + int(seconds)
                               + int(milliseconds) / 1000,
                               trn=int(state, 16)))
    if not events:
        raise ValueError(f'no TRAINPROGRESS events for {task} in {path}')
    return events


def compare_dwells(hardware, emulated):
    """Compare first visits with the same visible successor, without time alignment."""
    def visits(events):
        result = {}
        changes = []
        for event in events:
            if not changes or changes[-1]['trn'] != event['trn']:
                changes.append(event)
        for event, following in zip(changes, changes[1:]):
            result.setdefault(event['trn'],
                              (following['trn'], following['seconds']
                               - event['seconds']))
        return result
    real, replay = visits(hardware), visits(emulated)
    return [dict(state=state, next_state=next_state,
                 hardware_seconds=duration, emulator_seconds=replay[state][1],
                 difference_ms=1000 * (replay[state][1] - duration))
            for state, (next_state, duration) in real.items()
            if state in replay and replay[state][0] == next_state]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--law', choices=('pcma', 'pcmu'), required=True)
    parser.add_argument('--hardware-trace', type=Path)
    parser.add_argument('--hardware-task', default='P6,2')
    parser.add_argument('--apcm-native-rate', action='store_true',
                        help='diagnostic: switch to 8 kHz when APCM loads; '
                             'use with the 117-926 overlay override')
    args = parser.parse_args()
    data = args.capture.read_bytes()
    if not data:
        parser.error('capture is empty')
    hardware = (hardware_events(args.hardware_trace, args.hardware_task)
                if args.hardware_trace else [])
    drive.select_firmware_set('analog109')
    modem = AnalogKernelModem(modem_role='calling', law=args.law)
    modem.boot()
    modem.configure_modem('calling', args.law)
    previous = None
    events = []
    switched = None
    fields = {'trn': 0x3FC2, 'bootpage': 0x3FB0, 'rstatus_ch': 0x3FA3,
              'rstatus': 0x3FA4, 'speed': 0x3F62, 'speed_tx': 0x3F61}
    for index, code in enumerate(data):
        if (args.apcm_native_rate and switched is None
                and modem.card.resident == 0x026B):
            # Change only host clock cadence. Do not patch firmware, state,
            # or samples. The discarded converter tail is a known boundary
            # transient of this diagnostic.
            modem._to_codec = modem._to_bearer = None
            modem.codec_rate = 8000
            switched = index
        if args.law == 'pcma':
            word = drive.sport_rx_word(code, 'pcma')
            linear = (word - 0x10000 if word & 0x8000 else word) * 8
        else:
            linear = decode_mulaw(code)
        modem.frame_fast(linear & 0xFFFF, index)
        state = (modem.card.resident, modem.dm[0x3FC2])
        if state != previous:
            event = dict(sample=index, seconds=index / 8000,
                         overlay=modem.card.resident,
                         **{key: int(modem.dm[address])
                            for key, address in fields.items()})
            events.append(event)
            print(f'{index / 8000:8.4f} overlay={state[0]:04x} '
                  f'trn={state[1]:04x}', flush=True)
            previous = state
    result = dict(capture=str(args.capture),
                  sha256=hashlib.sha256(data).hexdigest(), samples=len(data),
                  harness='analog109', law=args.law, bearer_rate=8000,
                  initial_codec_rate=9600, apcm_rate_switch_sample=switched,
                  events=events, reached_data=any(e['trn'] == 0xD0 for e in events),
                  overlays={str(key): {'path': str(value[0]),
                                      'description': value[1]}
                            for key, value in modem.card.overlays.items()
                            if key in modem.card.served})
    if hardware:
        result['hardware_task'] = args.hardware_task
        result['hardware_events'] = hardware
        result['dwell_comparison'] = compare_dwells(hardware, events)
    args.out.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
