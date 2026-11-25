"""Require actual eight-endpoint CAPI data, complete teardown and resource reuse."""
import copy
import json
from pathlib import Path
import re
import sys
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from eicon_bri_guest import inspect_guest, inspect_calls, call_payload


class GuestCallsTest(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((ROOT / 'tests/data/bri_guest_calls_linux49.json').read_text())
        self.text, self.board = self.fixture['console_excerpt'], self.fixture['board']

    def test_actual_eight_endpoint_capture_passes_two_full_maximum_frame_cycles(self):
        result = inspect_guest(self.text, self.board)
        self.assertTrue(result['guest_idi'])
        self.assertTrue(result['guest_capi'])
        self.assertTrue(result['guest_calls'])
        self.assertEqual((result['capi_call_count'], result['capi_call_repeats']), (4, 2))
        self.assertEqual(sum(record['bytes'] for run in result['capi_runs'] for record in run['received']), 32768)
        for run in result['capi_runs']:
            self.assertEqual(run['baseline_entities'], [4] * 4)
            self.assertEqual(run['release_entities'], run['baseline_entities'])
            self.assertEqual(sorted(record['endpoint'] for record in run['received']), list(range(8)))
            self.assertGreater(run['irq_after'], run['irq_before'])

    def test_actual_64_cycle_capture_passes_full_soak_audit(self):
        fixture = json.loads((ROOT / 'tests/data/bri_guest_soak64_linux49.json').read_text())
        result = inspect_guest(fixture['console_excerpt'], fixture['board'])
        self.assertTrue(result['guest_fpga'])
        self.assertTrue(result['guest_power_cycle'])
        self.assertTrue(result['guest_idi'])
        self.assertTrue(result['guest_calls'])
        self.assertEqual(result['capi_call_repeats'], 64)
        self.assertEqual(sum(record['bytes'] for run in result['capi_runs'] for record in run['received']), 1048576)
        for run in result['capi_runs']:
            self.assertTrue(run['passed'])
            self.assertEqual(run['release_entities'], [4] * 4)

    def test_long_reconnect_audit_checks_every_round_and_rejects_stale_data(self):
        # Synthetic audit input exercises bounds; this is not a hardware soak.
        begins = list(re.finditer(r'^BRI_CAPI_CALL_BEGIN calls=4 round=', self.text, re.MULTILINE))
        template = self.text[begins[0].start():begins[1].start()]
        regions = []
        for round_index in range(1, 65):
            region = template.replace('round=1', f'round={round_index}')
            region = re.sub(r'(BRI_CAPI_DATA_OK endpoint=(\d+) bytes=2048 crc32=)[0-9a-f]+',
                lambda m: m[1] + f'{zlib.crc32(call_payload(int(m[2]) ^ 2, round_index)):08x}', region)
            region = re.sub(r'(^\s*11:\s+)\d+', lambda m: m[1] + str(1000 + round_index), region, flags=re.MULTILINE)
            regions.append(region)
        text = self.text[:begins[0].start()] + ''.join(regions)
        result = inspect_calls(text, self.board, True)
        self.assertTrue(result['guest_calls'])
        self.assertEqual(result['capi_call_repeats'], 64)
        stale = text.replace(f'{zlib.crc32(call_payload(2, 64)):08x}', f'{zlib.crc32(call_payload(2, 63)):08x}')
        self.assertFalse(inspect_calls(stale, self.board, True)['guest_calls'])
        for invalid in (0, 65, 256):
            with self.assertRaises(ValueError):
                call_payload(0, invalid)
        payloads = [call_payload(i, r) for r in range(1, 65) for i in range(8)]
        self.assertEqual(len(set(payloads)), 512)

    def test_missing_endpoint_corrupt_frame_or_stale_round_data_fails(self):
        missing = re.sub(r'^BRI_CAPI_DATA_OK endpoint=7 .*$', 'BRI_DATA_MISSING', self.text, count=1, flags=re.MULTILINE)
        corrupt = re.sub(r'crc32=[0-9a-f]+', 'crc32=00000000', self.text, count=1)
        records = re.findall(r'BRI_CAPI_DATA_OK endpoint=0 bytes=2048 crc32=([0-9a-f]+)', self.text)
        self.assertEqual(len(records), 2)
        stale = self.text.replace(records[1], records[0])
        for text in (missing, corrupt, stale):
            with self.subTest(kind=text[-100:]):
                result = inspect_guest(text, self.board)
                self.assertTrue(result['guest_capi'])
                self.assertFalse(result['guest_calls'])

    def test_resource_leak_or_no_call_interrupts_fails(self):
        marker = self.text.index('BRI_CAPI_RELEASE_INFO')
        tail = self.text[marker:]
        leaked = self.text[:marker] + re.sub(r'(E\. max/used\s*:\s*\d+/)\d+', r'\g<1>005', tail, count=1)
        interrupts = list(re.finditer(r'^\s*11:\s+(\d+).*DIVA 4BRI', self.text, re.MULTILINE))
        first_release = interrupts[3]
        frozen = self.text[:first_release.start(1)] + interrupts[2].group(1) + self.text[first_release.end(1):]
        for text in (leaked, frozen):
            self.assertFalse(inspect_guest(text, self.board)['guest_calls'])

    def test_eight_cumulative_activations_cannot_replace_eight_concurrent_bearers(self):
        match = re.search(r'^BRI_CAPI_BEARER_ACTIVE endpoint=0 .*\n', self.text, re.MULTILINE)
        without = self.text[:match.start()] + self.text[match.end():]
        boundary = re.search(r'^BRI_CAPI_MEDIA_OK.*\n', without, re.MULTILINE).end()
        reordered = without[:boundary] + match.group(0) + without[boundary:]
        result = inspect_guest(reordered, self.board)
        self.assertEqual(len(result['capi_runs'][0]['bearers']), 8)
        self.assertFalse(result['guest_calls'])

    def test_call_traffic_cannot_hide_missing_earlier_idi_interrupts(self):
        interrupts = list(re.finditer(r'^\s*11:\s+(\d+).*DIVA 4BRI', self.text, re.MULTILINE))
        idi = interrupts[2]
        text = self.text[:idi.start(1)] + interrupts[1].group(1) + self.text[idi.end(1):]
        result = inspect_guest(text, self.board)
        self.assertTrue(result['guest_calls'])
        self.assertFalse(result['guest_idi'])

    def test_wrong_wire_map_fails_and_all_payloads_are_distinct_full_octet_frames(self):
        board = copy.deepcopy(self.board)
        board['sport_peers']['0'] = 0
        self.assertFalse(inspect_guest(self.text, board)['guest_calls'])
        payloads = [call_payload(i, round_index) for round_index in (1, 2) for i in range(8)]
        self.assertEqual(len(set(payloads)), 16)
        self.assertEqual(len({zlib.crc32(p) for p in payloads}), 16)
        self.assertTrue(all(len(p) == 2048 and set(p) == set(range(256)) for p in payloads))


if __name__ == '__main__':
    unittest.main()
