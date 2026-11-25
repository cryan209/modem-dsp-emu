"""Replay measured power-state cycling without BAR accesses during D3hot."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from eicon_bri_card import BriCard
from eicon_bri_power_probe import cycle


class BriPowerTest(unittest.TestCase):
    def test_spare_power_cycle_retains_configuration_fpga_and_sdram(self):
        for filename in ('bri_power_card12196.json', 'bri_power_pending_card12196.json',
                         'bri_power_pending_masked_card12196.json',
                         'bri_power_pending_control_card12196.json'):
            with self.subTest(capture=filename):
                self.check_power_fixture(filename)

    def check_power_fixture(self, filename):
        fixture = json.loads((ROOT / 'tests/data' / filename).read_text())
        self.assertTrue(fixture['completed'])
        if 'config_d3' in fixture:
            self.assertEqual(fixture['config_before'], fixture['config_d3'])
        self.assertEqual(fixture['config_before'], fixture['config_after'])
        with BriCard() as card:
            card.ram[0x80000:0x80010] = bytes.fromhex(fixture['before']['sdram'])
            card.write(0, 0x69, 1, (fixture['before']['intcsr'] >> 8) & 0x7f)
            card.write(3, 0x400, 4, fixture['before']['irq'])
            def read(offset, size):
                return bytes(card.pci.read(offset+i, 1) for i in range(size))
            def write(offset, data):
                card.pci.write(offset, len(data), int.from_bytes(data, 'little'))
            def snapshot():
                self.assertEqual(card.pci.power_state, 0, 'BAR access outside D0')
                return dict(prom=card.read(0, 0x6e, 2), intcsr=card.read(0, 0x68, 4),
                    las0rr=card.read(0, 0, 4), lbrd0=card.read(0, 0x18, 4),
                    mabr0=card.read(0, 0xac, 4), risc=card.read(3, 0x200, 4),
                    irq=card.read(3, 0x400, 4), sdram=bytes(card.ram[0x80000:0x80010]).hex())
            if fixture.get('control'):
                result = dict(before=snapshot(), config_before=read(0, 64).hex(),
                              after=snapshot(), config_after=read(0, 64).hex(),
                              pmcsr_after=card.pci.power_state, completed=True)
            else:
                result = cycle(read, write, snapshot, sleep=lambda _: None)
            self.assertTrue(result['completed'])
            for name in ('pmcsr_before', 'pmcsr_d3', 'pmcsr_after'):
                if name in fixture:
                    self.assertEqual(result[name], fixture[name])
            for phase in ('before', 'after'):
                for name, expected in fixture[phase].items():
                    if name == 'intcsr': expected &= ~0x80  # Parity remains separately unqualified.
                    self.assertEqual(result[phase][name], expected, (phase, name))
            if 'config_d3' in result:
                self.assertEqual(result['config_before'], result['config_d3'])
            self.assertEqual(result['config_before'], result['config_after'])

    def test_probe_restores_d0_if_configuration_observation_fails(self):
        state = 0
        writes = []
        def read(offset, size):
            if offset == 0 and state == 3:
                raise OSError('observation failed')
            return (state if offset == 0x44 else 0).to_bytes(size, 'little')
        def write(offset, data):
            nonlocal state
            self.assertEqual(offset, 0x44)
            state = int.from_bytes(data, 'little')
            writes.append(state)
        with self.assertRaises(OSError):
            cycle(read, write, lambda: {}, sleep=lambda _: None)
        self.assertEqual(writes, [3, 0])
        self.assertEqual(state, 0)

    def test_pm_capability_rejects_unsupported_states_and_keeps_read_only_fields(self):
        with BriCard() as card:
            for width in (1, 2, 4):
                for state in (0, 3):
                    card.pci.write(0x44, width, state)
                    self.assertEqual(card.pci.power_state, state)
                    for unsupported in (1, 2):
                        card.pci.write(0x44, width, unsupported)
                        self.assertEqual(card.pci.power_state, state)
                card.pci.write(0x44, width, 0xffffffff & ((1 << (width*8))-1))
                self.assertEqual(card.pci.read(0x44, 4), 3)
                card.pci.write(0x44, width, 0)
                self.assertEqual(card.pci.read(0x44, 4), 0)


if __name__ == '__main__':
    unittest.main()
