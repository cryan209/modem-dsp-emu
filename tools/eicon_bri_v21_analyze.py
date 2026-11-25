"""Decode captured V.21 SPORT waveforms independently of modem firmware.

This diagnoses the serial boundary; decoded wire bytes do not imply a
firmware N_DATA indication. The UART uses fixed 300-baud 8N1 parameters,
without searching for an expected payload or fitting the baud rate.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np

WIRE_DTYPE = np.dtype([('tick', '<u8'), ('rx', '<u2', (8,)), ('tx', '<u4', (8,))])


def expand_g711(code, law):
    code = np.asarray(code, dtype=np.int32)
    if law == 'pcma':
        value = code ^ 0x55
        segment = (value >> 4) & 7
        magnitude = ((value & 15) << 4) + 8
        magnitude = np.where(segment, magnitude + 256, magnitude)
        magnitude = np.left_shift(magnitude, np.maximum(segment - 1, 0))
        return np.where(value & 128, magnitude, -magnitude)
    if law == 'pcmu':
        value = code ^ 255
        magnitude = (((value & 15) << 3) + 132) << ((value >> 4) & 7)
        magnitude -= 132
        return np.where(value & 128, -magnitude, magnitude)
    raise ValueError('unknown G.711 law')


def decode_8n1(samples, mark_hz, space_hz, rate=8000, baud=300):
    samples = np.asarray(samples, dtype=np.float64)
    if rate <= 0 or baud <= 0 or min(mark_hz, space_hz) <= 0 or max(mark_hz, space_hz) >= rate / 2:
        raise ValueError('invalid rate or tone frequencies')
    width = max(1, round(rate / baud * 0.9))
    if len(samples) < max(width, rate / baud * 10):
        return dict(data='', frames=[], framing_errors=0, window_samples=width)
    time = np.arange(len(samples)) / rate
    def energy(frequency):
        mixed = samples * np.exp(-2j * np.pi * frequency * time)
        return abs(np.convolve(mixed, np.ones(width), mode='same')) ** 2
    bits = energy(mark_hz) > energy(space_hz)
    starts = np.flatnonzero(bits[:-1] & ~bits[1:]) + 1
    period = rate / baud
    output, frames, errors, end = [], [], 0, 0
    for start in starts:
        if start < end or start + 10 * period >= len(samples):
            continue
        positions = np.rint(start + (np.arange(10) + 0.5) * period).astype(int)
        selected = bits[positions]
        if selected[0] or not selected[9]:
            errors += 1
            continue
        value = sum(int(selected[i + 1]) << i for i in range(8))
        output.append(value)
        frames.append(dict(start_sample=int(start), value=value))
        end = start + 9.8 * period
    return dict(data=bytes(output).hex(), frames=frames, framing_errors=errors, window_samples=width)


def spectrum(samples, rate=8000):
    samples = np.asarray(samples, dtype=np.float64)
    if len(samples) < 2 or not np.any(samples):
        return dict(rms=0, peak_hz=None)
    power = abs(np.fft.rfft((samples - samples.mean()) * np.hanning(len(samples))))
    index = int(np.argmax(power[1:]) + 1)
    return dict(rms=float(np.sqrt(np.mean(samples * samples))), peak_hz=index * rate / len(samples))


def analyze(prefix):
    prefix = Path(prefix)
    meta = json.loads(prefix.with_suffix('.wire.meta.json').read_text())
    if meta['profile'] != 0 or meta['modulation'].lower() != 'v21':
        raise ValueError('capture is not a V.21 modem probe')
    path = prefix.with_suffix('.wire.bin')
    if path.stat().st_size % WIRE_DTYPE.itemsize:
        raise ValueError('incomplete wire record')
    rows = np.fromfile(path, dtype=WIRE_DTYPE)
    if len(rows) < 4 or np.any(np.diff(rows['tick']) != 1):
        raise ValueError('capture requires consecutive word events')
    result = json.loads(prefix.with_suffix('.json').read_text())
    report = dict(records=len(rows), firmware_probe_completed=result['completed'],
                  firmware_received=result['received'], streams=[])
    peers = result['board']['sport_peers']
    delays = result['board']['sport_latency_words']
    for source, mark, space in ((1, 980, 1180), (3, 1650, 1850)):
        destination = peers[str(source)]
        delay = delays[str(destination)]
        outgoing = rows[rows['tick'] % 4 == 0]['tx'][:, source]
        incoming = rows[rows['tick'] % 4 == delay % 4]['rx'][:, destination]
        if np.any(outgoing == 0xffffffff):
            raise ValueError('DSP output absent during capture')
        tx = expand_g711((outgoing >> 8) & 255, meta['law'])
        rx = expand_g711((incoming >> 8) & 255, meta['law'])
        tx_uart = decode_8n1(tx, mark, space)
        rx_uart = decode_8n1(rx, mark, space)
        mismatches = int(np.count_nonzero(rows['rx'][delay:, destination] !=
                                         (rows['tx'][:-delay, source] & 65535)))
        expected = b'BRI' + bytes((source // 2,)) + bytes.fromhex(meta['payload'])
        report['streams'].append(dict(source_dsp=source, destination_dsp=destination,
            delay_words=delay, compared_words=len(rows) - delay, mismatched_words=mismatches,
            tx_spectrum=spectrum(tx), rx_spectrum=spectrum(rx), tx_uart=tx_uart, rx_uart=rx_uart,
            expected=expected.hex(), tx_matches=tx_uart['data'] == expected.hex(),
            rx_matches=rx_uart['data'] == expected.hex()))
    addresses = meta.get('dm_addresses', [])
    if addresses:
        dtype = np.dtype([('tick', '<u8'), ('words', '<u2', (8, len(addresses)))])
        path = prefix.with_suffix('.dm.bin')
        if path.stat().st_size % dtype.itemsize:
            raise ValueError('incomplete DM record')
        words = np.fromfile(path, dtype=dtype)
        report['dm_records'] = len(words)
        report['dm_addresses'] = addresses
        if 0x277b in addresses and len(words):
            column = addresses.index(0x277b)
            local_tones = {1: (980, 1180), 3: (1650, 1850)}
            for stream in report['streams']:
                core = stream['destination_dsp']
                samples = words['words'][:, core, column].copy().view(np.int16)
                peer_tones = local_tones[stream['source_dsp']]
                incoming = decode_8n1(samples, *peer_tones)
                local = decode_8n1(samples, *local_tones[core])
                expected_local = b'BRI' + bytes((core // 2,)) + bytes.fromhex(meta['payload'])
                stream['observed_dm_277b'] = dict(spectrum=spectrum(samples),
                    incoming_tone_uart=incoming, local_tone_uart=local,
                    matches_incoming_payload=incoming['data'] == stream['expected'],
                    matches_local_payload=local['data'] == expected_local.hex())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prefix', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    report = analyze(args.prefix)
    text = json.dumps(report, indent=2) + '\n'
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(json.dumps(dict(firmware_probe_completed=report['firmware_probe_completed'],
        streams=[{k: stream[k] for k in ('source_dsp', 'destination_dsp', 'mismatched_words',
                                        'tx_matches', 'rx_matches')} for stream in report['streams']])))


if __name__ == '__main__':
    main()
