"""Guest compatibility gates must distinguish attach, boot, and real IDI work."""
import copy
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from eicon_bri_guest import inspect_guest
from eicon_bri_guest_idi import build
import eicon_idi as idi


class BriGuestTest(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((ROOT / 'tests/data/bri_guest_idi_linux49.json').read_text())
        self.text = self.fixture['console_excerpt']
        self.board = self.fixture['board']

    def test_real_guest_capture_qualifies_all_four_controllers(self):
        result = inspect_guest(self.text, self.board)
        self.assertTrue(result['guest_driver_attached'])
        self.assertTrue(result['firmware_started'])
        self.assertTrue(result['guest_idi'])
        self.assertEqual(result['idi_entity_counts'], [1] * 4)
        self.assertEqual(len(result['firmware_controllers']), 4)
        self.assertEqual(result['guest_irq_counts'], [0, 2, 111])

    def test_real_vendor_capi_capture_qualifies_standard_listen_on_all_controllers(self):
        fixture = json.loads((ROOT / 'tests/data/bri_guest_vendor_capi_linux49.json').read_text())
        result = inspect_guest(fixture['console_excerpt'], fixture['board'])
        self.assertTrue(result['guest_driver_attached'])
        self.assertTrue(result['firmware_started'])
        self.assertTrue(result['guest_idi'])
        self.assertTrue(result['guest_capi'])
        self.assertEqual(result['capi_driver'], ['vendor-optimized'])
        self.assertEqual(result['capi_profiles'], [1, 2, 3, 4])
        self.assertEqual(result['capi_listen'], [1, 2, 3, 4])
        self.assertEqual(result['capi_errors'], [])

    def test_fpga_download_requires_measured_configuration_and_success_marker(self):
        fixture = json.loads((ROOT / 'tests/data/bri_guest_fpga_linux49.json').read_text())
        text, board = fixture['console_excerpt'], fixture['board']
        result = inspect_guest(text, board)
        for gate in ('guest_fpga', 'firmware_started', 'guest_idi', 'guest_capi', 'guest_calls'):
            self.assertTrue(result[gate], gate)
        self.assertFalse(inspect_guest(text.replace('BRI_FPGA_DOWNLOAD_OK', ''), board)['guest_fpga'])
        for field, value in (('done', False), ('clocks', 233651), ('length_count', 0),
                             ('personality', 'unknown'), ('error', 'configuration rejected')):
            with self.subTest(field=field):
                changed = copy.deepcopy(board)
                changed['fpga'][field] = value
                self.assertFalse(inspect_guest(text, changed)['guest_fpga'])

    def test_guest_power_cycle_requires_observed_transitions_and_ordered_confirmations(self):
        fixture = json.loads((ROOT / 'tests/data/bri_guest_power_linux49.json').read_text())
        text, board = fixture['console_excerpt'], fixture['board']
        report = inspect_guest(text, board)
        for gate in ('guest_power_cycle', 'guest_fpga', 'firmware_started', 'guest_idi', 'guest_calls'):
            self.assertTrue(report[gate], gate)
        for field, value in (('power_state', 3), ('power_transitions', []),
                             ('power_transitions', [3]), ('power_transitions', [0, 3]),
                             ('fault', 'bad power access')):
            with self.subTest(field=field, value=value):
                changed = copy.deepcopy(board)
                changed[field] = value
                self.assertFalse(inspect_guest(text, changed)['guest_power_cycle'])
        for marker in ('BRI_POWER_D3_OK', 'BRI_POWER_D0_OK'):
            self.assertFalse(inspect_guest(text.replace(marker, ''), board)['guest_power_cycle'])
        reordered = text.replace('BRI_POWER_D3_OK', 'BRI_POWER_SWAP').replace(
            'BRI_POWER_D0_OK', 'BRI_POWER_D3_OK').replace('BRI_POWER_SWAP', 'BRI_POWER_D0_OK')
        self.assertFalse(inspect_guest(reordered, board)['guest_power_cycle'])

    def test_attachment_and_success_markers_cannot_replace_firmware_evidence(self):
        for mutation in ('missing', 'signature', 'dsp', 'stopped', 'fault'):
            with self.subTest(mutation=mutation):
                board = copy.deepcopy(self.board)
                if mutation == 'missing':
                    board = {}
                elif mutation == 'signature':
                    board['controllers'][3]['signature'] = 0
                elif mutation == 'dsp':
                    board['allocated_dsps'].pop()
                elif mutation == 'stopped':
                    board['cpu_running'] = False
                else:
                    board['fault'] = 'unsupported access'
                result = inspect_guest(self.text, board)
                self.assertTrue(result['guest_driver_attached'])
                self.assertFalse(result['firmware_started'])
                self.assertFalse(result['guest_idi'])

    def test_missing_cycle_entity_leak_or_no_new_interrupts_fails_idi(self):
        missing = self.text.replace('BRI_IDI_REMOVE_OK adapter=3 cycle=2', 'BRI_IDI_TIMEOUT')
        match = list(re.finditer(r'^\s*11:\s+(\d+).*DIVA 4BRI', self.text, re.MULTILINE))[-1]
        no_irq = self.text[:match.start(1)] + '2' + self.text[match.end(1):]
        match = list(re.finditer(r'^E\. max/used\s*:\s*\d+/(\d+)', self.text, re.MULTILINE))[-1]
        leaked = self.text[:match.start(1)] + '002' + self.text[match.end(1):]
        for text in (missing, no_irq, leaked):
            with self.subTest(text=text[-160:]):
                result = inspect_guest(text, self.board)
                self.assertTrue(result['firmware_started'])
                self.assertFalse(result['guest_idi'])

    def test_capi_requires_every_profile_and_confirmation_without_driver_errors(self):
        markers = ''.join(f'BRI_CAPI_PROFILE_OK controller={i}\n'
                          f'BRI_CAPI_LISTEN_OK controller={i}\n' for i in range(1, 5))
        complete = self.text + markers + 'BRI_CAPI_CLIENT_OK\nBRI_CAPI_OK\n'
        self.assertTrue(inspect_guest(complete, self.board)['guest_capi'])
        cases = (complete.replace('BRI_CAPI_LISTEN_OK controller=3', 'BRI_CAPI_TIMEOUT'),
                 complete + 'BRI_CAPI_ERROR code=00001102\n',
                 complete + 'BRI_CAPI_FAILED status=4\n', self.text)
        for text in cases:
            with self.subTest(text=text[-100:]):
                self.assertFalse(inspect_guest(text, self.board)['guest_capi'])

    def test_requests_use_kernel_abi_and_compare_only_initialized_rc_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'idi'
            manifest = build(output)
            self.assertEqual(manifest['indication_header_size'], 152)
            self.assertEqual((output / 'adapter-4.bin').read_bytes(), struct.pack('<I', 4))
            assign = (output / 'assign.bin').read_bytes()
            payload = idi.sig_assign_payload()
            self.assertEqual(struct.unpack_from('<4I', assign), (0x20000002, 1, 0, len(payload)))
            self.assertEqual(assign[16:], payload)
            self.assertEqual((output / 'remove.bin').read_bytes(), struct.pack('<4I', 0x20000002, 255, 0, 0))
            self.assertEqual((output / 'assign-rc.bin').read_bytes(), struct.pack('<3I', 3, 239, 0))
            self.assertEqual((output / 'remove-rc.bin').read_bytes(), struct.pack('<3I', 3, 255, 0))
            self.assertEqual(len((output / 'assign-rc.bin').read_bytes()), 12)
            with self.assertRaises(FileExistsError):
                build(output)


if __name__ == '__main__':
    unittest.main()
