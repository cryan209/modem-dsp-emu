"""Capture raw SPORT peers during a real whole-board bearer probe.

Records contain a word-event tick, eight pre-event RX words and eight TX
latches with the core's TX-written bit. No words or firmware state change.
The default capture starts immediately after the host queues N_DATA.
"""
from __future__ import annotations
import argparse
import json
import struct
from pathlib import Path
from eicon_bri_card import BriCard
from eicon_bri_pair_probe import probe

RECORD = struct.Struct('<Q8H8I')
NO_OUTPUT = 0xffffffff


def capture(firmware_dir, output, *, instructions=430000000, words=96000,
            profile=0, modulation='v21', law='pcma', payload=b'whole-board-v21', dm_addresses=()):
    if words <= 0:
        raise ValueError('positive capture word count required')
    if any(not 0 <= address < 0x4000 for address in dm_addresses):
        raise ValueError('DM capture address outside DSP memory')
    dm_record = struct.Struct('<Q' + 'H' * (len(dm_addresses) * 8))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    armed = False
    ticks = written = 0
    first_tick = None
    with output.with_suffix('.wire.bin').open('wb') as stream, \
            output.with_suffix('.dm.bin').open('wb') as dm_stream:
        class CapturedCard(BriCard):
            def clock_sport1(self, *args, **kwargs):
                nonlocal ticks, written, first_tick
                ticks += 1
                if armed and written < words:
                    inputs = tuple(self.sport_input)
                    previous = tuple(self.sport_frames)
                    super().clock_sport1(*args, **kwargs)
                    outputs = tuple(self.sport_output[i] if self.sport_frames[i] != previous[i]
                                    else NO_OUTPUT for i in range(8))
                    stream.write(RECORD.pack(ticks, *inputs, *outputs))
                    if dm_addresses and ticks % 4 == 0:
                        values = []
                        for index in range(8):
                            core = self.dsps.get(index)
                            memory = core.lib.adsp2181_dm(core.cpu) if core else None
                            values.extend(memory[address] if memory is not None else 0xffff
                                          for address in dm_addresses)
                        dm_stream.write(dm_record.pack(ticks, *values))
                    written += 1
                    if first_tick is None:
                        first_tick = ticks
                else:
                    super().clock_sport1(*args, **kwargs)

        def progress(state):
            nonlocal armed
            armed = armed or state['data_posted']
            stream.flush()
            dm_stream.flush()
            with output.with_suffix('.progress.jsonl').open('a') as log:
                log.write(json.dumps(dict(instructions=state['instructions'], armed=armed,
                    written=written, received_bytes=state['received_bytes'],
                    network_connected=state['network_connected'], fault=state['fault'])) + '\n')

        output.with_suffix('.progress.jsonl').write_text('')
        result = probe(firmware_dir, instructions, profile=profile, network=True,
                       raw_sport=True, payload=payload, hangup=True,
                       modulation=modulation if not profile else None, law=law,
                       progress=progress, card_factory=CapturedCard)
    output.with_suffix('.json').write_text(json.dumps(result, indent=2) + '\n')
    output.with_suffix('.wire.meta.json').write_text(json.dumps(dict(
        format=RECORD.format, record_bytes=RECORD.size, records=written,
        first_tick=first_tick, no_output=NO_OUTPUT, dsp_indices=list(range(8)),
        words_per_iom_frame=4, functional_samples_per_second=8000,
        profile=profile, modulation=modulation if not profile else None, law=law,
        dm_format=dm_record.format, dm_addresses=list(dm_addresses),
        dm_sampling='end of each four-word IOM frame; DSP-major address order',
        payload=payload.hex(), probe_completed=result['completed']), indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware-dir', type=Path, default=Path('docs/firmware'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--instructions', type=int, default=430000000)
    parser.add_argument('--words', type=int, default=96000)
    parser.add_argument('--profile', type=int, default=0)
    parser.add_argument('--modulation', default='v21')
    parser.add_argument('--law', choices=('pcmu', 'pcma'), default='pcma')
    parser.add_argument('--payload', default='whole-board-v21')
    parser.add_argument('--dm', default='', help='comma-separated DM word addresses to sample each IOM frame')
    args = parser.parse_args()
    result = capture(args.firmware_dir, args.out, instructions=args.instructions,
                     words=args.words, profile=args.profile, modulation=args.modulation,
                     law=args.law, payload=args.payload.encode(),
                     dm_addresses=tuple(int(value, 0) for value in args.dm.split(',') if value))
    print(json.dumps({k: result.get(k) for k in ('completed', 'stage', 'fault', 'senders')}))
    return 0 if result['completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
