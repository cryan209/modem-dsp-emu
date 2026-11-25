"""Driver contract tests for the 4BRI v2 board boundary."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from eicon_bri_card import BriCard, INSTANCE_SIZE, SHARED_SIZE


class BriCardTest(unittest.TestCase):
    def test_raw_sport_peers_sample_both_directions_before_propagation(self):
        with BriCard() as card:
            card.write(3, 0, 4, 0x1f)
            sampled = []
            for index, output in ((1, 0x1234), (3, 0xabcd)):
                core = card._core(index)
                def frame(word, cycles, index=index, output=output):
                    sampled.append((index, word))
                    return 0x10000 | output
                core.sport1 = frame
                card.ipacs[index // 2].iom_configured = True
            card.connect_sport1(1, 3)
            card.clock_sport1()
            self.assertEqual(sampled, [(1, 0xffff), (3, 0xffff)])
            card.clock_sport1()
            self.assertEqual(sampled[-2:], [(1, 0xabcd), (3, 0x1234)])
            with self.assertRaises(ValueError):
                card.connect_sport1(1, 5)
            with self.assertRaises(ValueError):
                card.connect_sport1(4, 4)
            # Reset/disabled source must not replay an old transmit latch.
            card.device_reset = 3
            card.clock_sport1()
            self.assertEqual(card.sport_input[1], 0xffff)

    def test_raw_sport_two_word_delay_preserves_both_serial_streams(self):
        with BriCard() as card:
            card.write(3, 0, 4, 0x1f)
            sampled = {1: [], 3: []}
            for index in (1, 3):
                core = card._core(index)
                def frame(word, cycles, index=index):
                    sampled[index].append(word)
                    return 0x10000 | (index << 8) | len(sampled[index])
                core.sport1 = frame
                card.ipacs[index // 2].iom_configured = True
            card.connect_sport1(1, 3, latency_words=2)
            for _ in range(4):
                card.clock_sport1()
            self.assertEqual(sampled[1], [0xffff, 0xffff, 0x301, 0x302])
            self.assertEqual(sampled[3], [0xffff, 0xffff, 0x101, 0x102])
            with self.assertRaises(ValueError):
                card.connect_sport1(4, 5, latency_words=0)

    def test_iom_frames_keep_later_enabled_ports_on_word_boundaries(self):
        with BriCard() as card:
            card.write(3, 0, 4, 0x1f)
            calls = {1: 0, 3: 0}
            for index in calls:
                def frame(word, cycles, index=index):
                    calls[index] += 1
                    return 0x1ffff
                card._core(index).sport1 = frame
            card.ipacs[0].iom_configured = True
            card.clock_iom_frame()
            self.assertEqual(calls, {1: 4, 3: 0})
            card.ipacs[1].iom_configured = True
            card.clock_iom_frame()
            self.assertEqual(calls, {1: 8, 3: 4})
            self.assertEqual(card.sport_frames[1], 8)
            self.assertEqual(card.sport_frames[3], 4)

    def test_four_controller_windows_share_bar2_without_aliasing(self):
        with BriCard() as card:
            for controller in range(4):
                window = card.controller_memory(controller, shared=True)
                self.assertEqual(len(window), SHARED_SIZE)
                window[0x1e:0x20] = (0x4447 + controller).to_bytes(2, 'little')
            for controller in range(4):
                offset = (controller + 1) * INSTANCE_SIZE - SHARED_SIZE + 0x1e
                self.assertEqual(card.read(2, offset, 2), 0x4447 + controller)
                self.assertEqual(card.read(2, controller * INSTANCE_SIZE, 4), 0)

    def test_driver_start_irq_test_isr_ack_and_stop(self):
        with BriCard() as card:
            card.write(3, 0x200, 4, 1)
            self.assertFalse(card.cpu_running)
            card.write(3, 0x200, 4, 3)
            self.assertTrue(card.cpu_running)
            # CPU release is not a firmware boot result.
            self.assertEqual(bytes(card.controller_memory(0, True)[0x1e:0x20]), b'\0\0')
            card.write(3, 0x400, 4, 1)
            self.assertEqual(card.read(0, 0x69, 1), 0)
            self.assertFalse(card.irq_asserted)
            card.write(0, 0x69, 1, 9)
            self.assertTrue(card.irq_asserted)
            self.assertEqual(card.read(0, 0x69, 1), 0x89)
            card.write(3, 0x400, 4, 0)
            self.assertFalse(card.irq_asserted)
            card.raise_interrupt()
            self.assertTrue(card.irq_asserted)
            card.write(0, 0x69, 1, 0)
            self.assertFalse(card.irq_asserted)
            card.write(3, 0x200, 4, 0)
            self.assertFalse(card.cpu_running)

    def test_real_idma_keeps_all_eight_cores_independent(self):
        with BriCard() as card:
            card.write(3, 0, 4, 1)
            for index in range(8):
                base = 0x1800 + (index // 2) * 0x1000 + (index % 2) * 0x10
                card.write(3, base + 8, 2, 0x4100)
                card.write(3, base, 2, 0xA500 + index)
                card.write(3, base, 2, 0x1234 + index)
            for index in range(8):
                base = 0x1800 + (index // 2) * 0x1000 + (index % 2) * 0x10
                card.write(3, base + 8, 2, 0x4100)
                self.assertEqual(card.read(3, base, 2), 0xA500 + index)
                self.assertEqual(card.read(3, base, 2), 0x1234 + index)
            self.assertEqual(len(card.dsps), 8)
            self.assertEqual(card.run_dsps(100), 0, 'DM writes must not release boot hold')

    def test_pm_idma_uses_two_transfers_per_word_and_boot_release(self):
        with BriCard() as card:
            card.write(3, 0, 4, 1)
            base = 0x1800
            # IDLE at PM0: low octet must survive the second transfer.
            card.write(3, base + 8, 2, 0)
            card.write(3, base, 2, 0x0280)
            self.assertEqual(card.run_dsps(10), 0)
            card.write(3, base, 2, 0x00)
            card.write(3, base + 8, 2, 0)
            self.assertEqual(card.read(3, base, 2), 0x0280)
            self.assertEqual(card.read(3, base, 2), 0)
            self.assertGreater(card.run_dsps(10), 0)

    def test_stage_actual_four_firmware_images_at_their_linked_addresses(self):
        root = Path(__file__).resolve().parents[1] / 'docs/firmware'
        if not (root / 'te_dmlt.2q0').exists():
            self.skipTest('protocol firmware unavailable')
        with BriCard() as card:
            for controller in range(4):
                image = root / f'te_dmlt.2q{controller}'
                card.stage_protocol(controller, image)
                self.assertEqual(bytes(card.controller_memory(controller)[:image.stat().st_size]),
                                 image.read_bytes())
            with self.assertRaises(ValueError):
                card.stage_protocol(1, root / 'te_dmlt.2q0')
            card.write(3, 0x200, 4, 3)
            with self.assertRaises(RuntimeError):
                card.stage_protocol(0, root / 'te_dmlt.2q0')
            self.assertEqual([c['signature'] for c in card.snapshot()['controllers']], [0]*4)

    def test_runtime_pm_download_does_not_reenter_boot_hold(self):
        with BriCard() as card:
            card.write(3, 0, 4, 1)
            # Boot at IDLE, then patch a different PM word while running.
            card.write(3, 0x1808, 2, 0)
            card.write(3, 0x1800, 2, 0x0280)
            card.write(3, 0x1800, 2, 0)
            self.assertGreater(card.run_dsps(10), 0)
            card.write(3, 0x1808, 2, 0x100)
            card.write(3, 0x1800, 2, 0x1234)
            card.write(3, 0x1800, 2, 0x56)
            self.assertGreater(card.run_dsps(10), 0)

    def test_reentrant_loader_selects_etsi_for_each_dmlt_port(self):
        root = Path(__file__).resolve().parents[1] / 'docs/firmware'
        with BriCard(reentrant=True) as card:
            card.load_reentrant(root / 'te_dmlt.2qm', root / 'dspdload.bin')
            for controller in range(4):
                self.assertEqual(card.controller_memory(controller, True)[19], 0x81)

    def test_memory_widths_bounds_and_unknown_registers(self):
        with BriCard() as card:
            card.write(2, 0, 4, 0x12345678)
            self.assertEqual(card.read(2, 0, 1), 0x78)
            self.assertEqual(card.read(2, 2, 2), 0x1234)
            for args in ((2, -1, 1), (2, 1, 2), (2, 0x1000000, 1)):
                with self.assertRaises(ValueError):
                    card.read(*args)
            with self.assertRaises(NotImplementedError):
                card.read(3, 0x1802, 2)
            self.assertEqual(card.dsps, {}, 'invalid register must not create a phantom DSP')
            with self.assertRaises(ValueError):
                card.controller_memory(4)
        with self.assertRaises(RuntimeError):
            card.read(2, 0, 1)
        card.close()


if __name__ == '__main__':
    unittest.main()
