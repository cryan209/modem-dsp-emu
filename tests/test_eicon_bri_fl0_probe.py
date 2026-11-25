"""Validate flag-matrix stimulus independently of a guessed IRQ mapping."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from eicon_bri_fl0_probe import build
from eicon_dsp_bench import read_results


class Fl0ProbeTest(unittest.TestCase):
    def test_physical_matrix_acknowledges_every_command(self):
        image, meta = build()
        fixture = json.loads((Path(__file__).parent / 'data/bri_fl0_card12196.json').read_text())
        self.assertTrue(fixture['finished'])
        self.assertIn('card 12196 (0000:02:02.0)', fixture['source'])
        self.assertEqual(hashlib.sha256(image).hexdigest(), fixture['program_sha256'])
        self.assertEqual(meta['low_masks'], fixture['low_masks'])
        self.assertEqual(len(fixture['phases']), 19)
        for phase in fixture['phases']:
            values = [int(v, 16) for v in phase['values']]
            self.assertEqual(values[6:], [(phase['low_mask'] >> i) & 1 for i in range(8)])
        # Latched Cause changes are observations, not an asserted mapping.

    def test_probe_actually_drives_flags_instead_of_only_echoing_commands(self):
        from eicon_bri_card import BriCard
        from eicon_bri_machine import BriMachine
        image, meta = build()
        with BriCard() as card:
            card.ram[:len(image)] = image
            machine = BriMachine(card)
            card.write(3, 0x200, 4, 3)
            # This unchanged 15 KiB matrix has no reset-to-SDRAM trampoline.
            # Exercise its flag stimulus from uncached SDRAM, not the limited
            # reset alias. Dedicated machine tests qualify reset entry; this
            # test must not infer a larger aperture from a successful matrix.
            result = machine.run(10000000, start=0xA0000000)
            self.assertIsNone(result['fault'])
            self.assertEqual(bytes(card.ram[:len(image)]), image)
            captured = read_results(card.ram, meta)
            self.assertTrue(captured['finished'])
            self.assertEqual(len(captured['dsps']), len(meta['low_masks']))
            for mask, phase in zip(meta['low_masks'], captured['dsps']):
                self.assertEqual([int(v, 16) for v in phase['dm'][6:]],
                                 [(mask >> i) & 1 for i in range(8)])
            # Comparing AX0 instead of AR still echoes all commands but never
            # lowers a pin. Count the actual executed RESET FL0 transitions.
            self.assertEqual([card.dsps[i].lib.adsp2181_fl0_falls(card.dsps[i].cpu)
                              for i in range(8)], [3] * 7 + [1])

    def test_physical_cir_ack_clears_its_cpu_interrupt_and_preserves_stop_go(self):
        from eicon_bri_card import BriCard
        from eicon_bri_machine import BriMachine
        image, meta = build(status_ack=True)
        fixture = json.loads((Path(__file__).parent / 'data/bri_fl0_status_ack_card12196.json').read_text())
        self.assertTrue(fixture['finished'])
        self.assertEqual(hashlib.sha256(image).hexdigest(), fixture['program_sha256'])
        with BriCard() as card:
            machine = BriMachine(card)
            device = card.ipacs[0]
            # Drain power-on indications before replaying the observed
            # external C/I transitions. Other interrupt sources stay masked.
            device.read(0x2e)
            device.serial_tick()
            device.read(0x2e)
            device.write(0x60, 0xef)
            for phase in fixture['phases']:
                values = [int(v, 16) for v in phase['values']]
                self.assertEqual(values[26:34], [(phase['low_mask'] >> i) & 1 for i in range(8)])
                cir = values[14]
                if cir & 8:
                    device._indication(cir >> 4)
                    device.serial_tick()
                # Mask only physically identified IPAC sources; IP4/IP7
                # background levels are not inferred from this experiment.
                self.assertEqual(machine._cause() & 0x6c00, values[0] & 0x6c00)
                self.assertEqual(device.read(0x60), values[2])
                self.assertEqual(device.read(0x2e), cir)
                self.assertEqual(machine._cause() & 0x6c00, values[34] & 0x6c00)


if __name__ == '__main__':
    unittest.main()
