"""Qualify BRI channel packing, companding and real-kernel task registration."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import dial_tikrnl_drive as drive
from eicon_bri_caller_replay import (ADSP, BriCallerReplay, BriKernelModem,
                                    KERNEL, ROOT)


@unittest.skipUnless(KERNEL.is_dir() and (ROOT / 'overlays').is_dir(),
                     'extract card-type 53 BRI firmware first')
class BriCallerReplayTest(unittest.TestCase):
    def test_live_adapter_preserves_every_wire_octet_through_firmware_encoder(self):
        # The BRI ring already contains G.711 octets. Passing through the
        # SIP PCM16 boundary must not collapse the two PCMU zero codes.
        # Use the real resident encoder, not a second copy of the decoder.
        from eicon_adsp_sip import EiconSipEndpoint
        for law in ('pcmu', 'pcma'):
            with self.subTest(law=law):
                drive.select_firmware_set('pri117')
                codec = drive.Card(serve=False)
                codec.boot()
                codec.configure_g711_law(law)
                adapter = BriKernelModem.__new__(BriKernelModem)
                adapter.driver = SimpleNamespace(
                    law=law, tx_code=0, frame=lambda code: None,
                    service=lambda index: None)
                linear = []
                for code in range(256):
                    adapter.driver.tx_code = code
                    linear.append(adapter.frame_fast(0, code))
                endpoint = SimpleNamespace(
                    law=law, codec=codec, rtp_packet_samples=160,
                    tx_target_quanta=1, service_transmit=lambda *args: None)
                call = SimpleNamespace(raw_tx_codes=None, tx_pending=bytearray(),
                                       tx_queue=[])
                EiconSipEndpoint._queue_rtp(endpoint, call, linear)
                wire = b''.join(call.tx_queue) + bytes(call.tx_pending)
                self.assertEqual(wire, bytes(range(256)))

    def test_host_completion_services_the_v90d_delay_line_contract(self):
        replay = BriCallerReplay('pcmu')
        card = replay.card
        # Isolate the host ABI from DSP execution: a resident page supplies
        # distinct near/far pairs, which the next frame must receive delayed.
        card.resident = drive.V90D_ID
        card.dm[0x3FC1] = 0x0400
        card.dm[0x3F04] = 0
        near, far = drive.V90D_BULK_SEED_BASE, (
            drive.V90D_BULK_SEED_BASE + drive.V90D_BULK_SEED_SPAN)
        for index in range(far + 3):
            card.dm[0x3FBE], card.dm[0x3FBF] = index + 1, index + 1001
            replay.service(index)
            self.assertEqual((card.dm[0x3FBC], card.dm[0x3608]), (near, near))
            self.assertEqual((card.dm[0x3FBD], card.dm[0x3609]), (far, far))
            self.assertEqual((card.dm[0x3F36], card.dm[0x3F37]),
                (index - near + 1, index - near + 1001) if index >= near else (0, 0))
            self.assertEqual((card.dm[0x3F38], card.dm[0x3F39]),
                (index - far + 1, index - far + 1001) if index >= far else (0, 0))

    def tearDown(self):
        drive.select_firmware_set('pri117')

    def test_kernel_registers_task_and_starts_v8_without_state_pins(self):
        replay = BriCallerReplay('pcma')
        replay.boot()
        self.assertEqual(replay.card.pm[0x02B0], 0x1C6FCF)
        self.assertEqual(replay.card.pm[0x0072], 0x1C8F6F)
        for index in range(100):
            replay.frame(0xD5)
            replay.service(index)
        self.assertEqual(replay.card.resident, 0x025F)
        self.assertEqual(replay.card.dm[0x3FC2], 1)

    def test_all_alaw_codes_enter_the_correct_channel_in_native_units(self):
        replay = BriCallerReplay('pcma')
        card = replay.card
        card._download(str(KERNEL))
        ADSP.adsp2181_run(card.cpu, 5000)
        ADSP.adsp2181_set_callbacks(card.cpu, *replay.callbacks)
        card.dm[0x2DF9] = card.dm[0x2F03] = 0x3C37
        for code in range(256):
            # Independent opposite code on channel one detects byte swaps.
            replay.word = ((255 - code) << 8) | code
            produced = []
            for _ in range(4):
                old = card.dm[0x2DE0]
                ADSP.adsp2181_sport1_frame(card.cpu, replay.word, 1)
                for tick in range(1000):
                    if ADSP.adsp2181_pc(card.cpu) == 0x028C:
                        break
                    ADSP.adsp2181_run(card.cpu, 1)
                else:
                    self.fail('BRI SPORT1 ISR did not return')
                pointer = card.dm[0x2DE0]
                if pointer != old:
                    # Read before the foreground overwrites the ring with TX.
                    produced.append([card.dm[0x2E00 + ((pointer - 0x2E00 - 2 + i) % 128)]
                                     for i in range(2)])
                ADSP.adsp2181_run(card.cpu, 300000)
            self.assertEqual(len(produced), 1, 'one bearer pair per four words')
            self.assertEqual(produced[0],
                             [drive.sport_rx_word(255 - code, 'pcma'),
                              drive.sport_rx_word(code, 'pcma')])

    def test_duplex_tx_uses_selected_channel_and_companded_ring_output(self):
        replay = BriCallerReplay('pcmu')
        card = replay.card
        card._download(str(KERNEL))
        ADSP.adsp2181_run(card.cpu, 5000)
        ADSP.adsp2181_set_callbacks(card.cpu, *replay.callbacks)
        for _ in range(4):
            replay.frame(255)
        card.dm[0x2F03] = 0x3C27
        # A constant foreground producer isolates the real TX ring and ISR.
        # It replaces the empty dispatch, not modem firmware in a live call.
        for sample in (-8191, -1234, -1, 0, 1, 1234, 8191):
            card.pm[0x02B0] = 0x40000F | ((sample & 0xFFFF) << 4)
            # The shared 128-word/two-channel ring delays TX by 64 frames.
            for _ in range(66):
                replay.frame(255)
            # Kernel PM0076..0079 normalizes the reserved 7e code to
            # negative zero (7f) when its linear producer supplied ffff.
            expected = 0x7F if sample == -1 else drive.linear_to_mulaw(sample * 4)
            self.assertEqual(replay.tx_code, expected)


if __name__ == '__main__':
    unittest.main()
