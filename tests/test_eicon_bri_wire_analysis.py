"""Independent fixed-parameter V.21 waveform/UART diagnostic checks."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from eicon_bri_v21_analyze import WIRE_DTYPE, analyze, decode_8n1, expand_g711


class WireAnalysisTest(unittest.TestCase):
    def test_fixed_uart_decoder_recovers_every_byte_on_both_v21_channels(self):
        payload = bytes(range(256))
        bits = [1] * 20
        for value in payload:
            bits += [0] + [(value >> bit) & 1 for bit in range(8)] + [1]
        bits += [1] * 20
        sample_bits = np.asarray(bits)[(np.arange(int(len(bits) * 8000 / 300)) * 300 / 8000).astype(int)]
        for mark, space in ((980, 1180), (1650, 1850)):
            frequency = np.where(sample_bits, mark, space)
            phase = np.cumsum(frequency) * (2 * np.pi / 8000)
            decoded = decode_8n1(5000 * np.sin(phase), mark, space)
            self.assertEqual(bytes.fromhex(decoded['data']), payload)
            self.assertEqual(len(decoded['frames']), 256)
            self.assertEqual(decoded['framing_errors'], 0)

    def test_idle_and_incomplete_frames_do_not_invent_bytes(self):
        self.assertEqual(decode_8n1(np.zeros(1000), 980, 1180)['data'], '')
        self.assertEqual(decode_8n1(np.ones(20), 980, 1180)['data'], '')
        time = np.arange(1000) / 8000
        self.assertEqual(decode_8n1(np.sin(2 * np.pi * 980 * time), 980, 1180)['data'], '')
        self.assertEqual(expand_g711([0xd5, 0x55, 0xaa, 0x2a], 'pcma').tolist(),
                         [8, -8, 32256, -32256])
        self.assertEqual(expand_g711([0xff, 0x7f, 0, 0x80], 'pcmu').tolist(),
                         [0, 0, -32124, 32124])

    def test_wire_corruption_and_firmware_result_are_reported_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / 'capture'
            rows = np.zeros(400, dtype=WIRE_DTYPE)
            rows['tick'] = np.arange(1, 401)
            rows['rx'] = 0xffff
            rows['tx'] = 0x1ffff
            rows['rx'][100, 3] = 0xfffe
            rows.tofile(prefix.with_suffix('.wire.bin'))
            prefix.with_suffix('.wire.meta.json').write_text(json.dumps(dict(
                profile=0, modulation='v21', law='pcma', payload=b'hello'.hex())))
            prefix.with_suffix('.json').write_text(json.dumps(dict(completed=False,
                received={'0': '', '1': ''}, board=dict(sport_peers={'1': 3, '3': 1},
                sport_latency_words={'1': 2, '3': 2}))))
            result = analyze(prefix)
            self.assertFalse(result['firmware_probe_completed'])
            self.assertEqual([s['mismatched_words'] for s in result['streams']], [1, 0])
            self.assertFalse(any(s['tx_matches'] for s in result['streams']))


if __name__ == '__main__':
    unittest.main()
