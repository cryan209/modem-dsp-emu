"""Retained-state handoff for the native echo bulk-delay worker."""
import contextlib
import io
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

try:
    import eicon_mips_shim as shim
except ImportError as exc:            # unicorn is not installed
    shim = None
    _import_error = exc


@unittest.skipIf(shim is None, "eicon_mips_shim needs unicorn")
class BulkDescriptorTests(unittest.TestCase):
    def test_zero_based_descriptor_gets_minus_one_lower_limit(self):
        dm = [0] * 0x4000

        address = shim.publish_bulk_lower_limit(dm)

        self.assertEqual(address, 5)
        self.assertEqual(dm[5], 0xFFFF)
        self.assertEqual(dm[4], 0)
        self.assertEqual(dm[6], 0)

    def test_publication_follows_the_firmware_descriptor_selector(self):
        dm = [0] * 0x4000
        dm[0x32F7] = 8

        address = shim.publish_bulk_lower_limit(dm)

        self.assertEqual(address, 13)
        self.assertEqual(dm[5], 0)
        self.assertEqual(dm[13], 0xFFFF)

    def test_descriptor_address_uses_the_adsp_14_bit_domain(self):
        dm = [0] * 0x4000
        dm[0x32F7] = 0x3FFD

        address = shim.publish_bulk_lower_limit(dm)

        self.assertEqual(address, 2)
        self.assertEqual(dm[2], 0xFFFF)


@unittest.skipIf(shim is None, "eicon_mips_shim needs unicorn")
class V90DBulkReleaseTests(unittest.TestCase):
    def setUp(self):
        self.dm = [0] * 0x4000

    def test_rate_must_be_published(self):
        self.dm[0x1E4F] = 21

        self.assertIsNone(shim.v90d_bulk_adapter_parameters(self.dm))

    def test_no_width_releases_without_a_repeatable_hardware_proof(self):
        for count in (21, 31, 32, 42):
            with self.subTest(count=count):
                self.dm[0x3F61] = 0x2020 + count - 21
                self.dm[0x1E4F] = count
                self.assertIsNone(
                    shim.v90d_bulk_adapter_parameters(self.dm))

    def test_upstream_rate_cannot_release_downstream_worker(self):
        self.dm[0x3F62] = 0x11E9
        self.dm[0x1E4F] = 32

        self.assertIsNone(shim.v90d_bulk_adapter_parameters(self.dm))

    def test_rate_and_count_must_agree(self):
        self.dm[0x3F61] = 0x202B
        self.dm[0x1E4F] = 31

        self.assertIsNone(shim.v90d_bulk_adapter_parameters(self.dm))

    def test_rate_word_must_have_v90_format(self):
        self.dm[0x3F61] = 0x002B
        self.dm[0x1E4F] = 32

        self.assertIsNone(shim.v90d_bulk_adapter_parameters(self.dm))


@unittest.skipIf(shim is None, "eicon_mips_shim needs unicorn")
class PortableBulkDelayTests(unittest.TestCase):
    def setUp(self):
        self.dm = [0] * 0x4000
        self.delay = shim.PortableBulkDelay()

    def test_near_and_far_outputs_are_delayed_in_pair_clocks(self):
        self.dm[0x3FBC] = 2
        self.dm[0x3FBD] = 3
        outputs = []
        for value in (1, 2, 3, 4):
            self.dm[0x3FBE] = value
            self.dm[0x3FBF] = 0x1000 + value
            self.assertTrue(self.delay.service(self.dm))
            outputs.append(tuple(self.dm[a] for a in range(0x3F36, 0x3F3A)))

        self.assertEqual(outputs, [
            (0, 0, 0, 0),
            (0, 0, 0, 0),
            (1, 0x1001, 0, 0),
            (2, 0x1002, 1, 0x1001),
        ])

    def test_length_change_flushes_old_samples(self):
        self.dm[0x3FBC] = self.dm[0x3FBD] = 1
        self.dm[0x3FBE], self.dm[0x3FBF] = 0x1234, 0x5678
        self.delay.service(self.dm)
        self.delay.service(self.dm)
        self.assertEqual((self.dm[0x3F36], self.dm[0x3F37]),
                         (0x1234, 0x5678))

        self.dm[0x3FBC], self.dm[0x3FBD] = 2, 3
        self.delay.service(self.dm)
        self.assertEqual(tuple(self.dm[a] for a in range(0x3F36, 0x3F3A)),
                         (0, 0, 0, 0))

    def test_invalid_descriptor_fails_closed_and_clears_outputs(self):
        for near, bulk in ((0, 1), (2, 1), (1, 0x2001), (0xFFFF, 1)):
            with self.subTest(near=near, bulk=bulk):
                self.dm[0x3FBC], self.dm[0x3FBD] = near, bulk
                for address in range(0x3F36, 0x3F3A):
                    self.dm[address] = 0xAAAA
                self.assertFalse(self.delay.service(self.dm))
                self.assertEqual(
                    tuple(self.dm[a] for a in range(0x3F36, 0x3F3A)),
                    (0, 0, 0, 0))

    def test_the_per_frame_dispatch_vector_is_never_written(self):
        # PM 0x19f3/0x19f4 do `I4 = DM(0x3FB8); CALL (I4)` every frame, and the
        # firmware holds 0x3cea there -- code that sets the DM(0x3fc1) 0x0400
        # worker-enable bit and jumps to the generator dispatch at 0x2a56.
        # Session 111 mapped the near/far outputs onto 0x3fb6..0x3fb9 and wrote
        # samples over it, which called the page into garbage: the generator
        # stopped, TX datagrams stayed 0/0, and the outer state never left
        # 0x0050 for the whole call.
        sentinels = {0x3FB6: 0x1111, 0x3FB7: 0x2222,
                     0x3FB8: 0x3CEA, 0x3FB9: 0x1000}
        for address, value in sentinels.items():
            self.dm[address] = value
        self.dm[0x3FBC], self.dm[0x3FBD] = 2, 3
        self.dm[0x3FBE], self.dm[0x3FBF] = 0x1234, 0x5678

        for _ in range(6):
            self.delay.service(self.dm)
        self.dm[0x3FBC] = 0                       # and on the failing path too
        self.delay.service(self.dm)

        for address, value in sentinels.items():
            self.assertEqual(self.dm[address], value,
                             f"DM({address:#06x}) was overwritten")

    def test_modem_service_keeps_native_tail_held_and_runs_before_rate(self):
        self.dm[0x3FC1] = 0x0400
        self.dm[0x3FBC] = self.dm[0x3FBD] = 1
        self.dm[0x3FBE], self.dm[0x3FBF] = 0x1234, 0x5678
        modem = types.SimpleNamespace(
            resident=0x026A, dm=self.dm, _bulk_adapter_held=True,
            _portable_bulk_delay=self.delay, _portable_bulk_active=False)

        with contextlib.redirect_stdout(io.StringIO()):
            shim.NativeMipsModem._service_bulk_adapter(modem)
            shim.NativeMipsModem._service_bulk_adapter(modem)

        self.assertTrue(modem._bulk_adapter_held)
        self.assertTrue(modem._portable_bulk_active)
        self.assertEqual((self.dm[0x3F36], self.dm[0x3F37]),
                         (0x1234, 0x5678))
        self.assertEqual(self.dm[0x3F61], 0)  # no rate was needed

@unittest.skipIf(shim is None, "eicon_mips_shim needs unicorn")
class BulkDelaySeedTests(unittest.TestCase):
    """PM 0x3232..0x3243 and PM 0x1085/0x1086, reproduced at service time."""

    def setUp(self):
        self.dm = [0] * 0x4000
        self.dm[0x3F04] = 0x000C          # delaycorrection, as shipped

    def test_no_seed_before_a_round_trip_has_been_measured(self):
        self.assertIsNone(shim.bulk_delay_seed(self.dm))

    def test_seed_matches_the_firmware_arithmetic(self):
        self.dm[0x3FCB] = 0x01A6          # the live v90-bulk-dm5 measurement

        self.assertEqual(shim.bulk_delay_seed(self.dm), (0x01D7, 0x0227))

    def test_negative_measurement_is_not_a_delay(self):
        self.dm[0x3FCB] = 0xFF7B

        self.assertIsNone(shim.bulk_delay_seed(self.dm))

    def test_both_lengths_honour_the_firmware_ceiling(self):
        self.dm[0x3FCB] = 0x0AFF

        near, far = shim.bulk_delay_seed(self.dm)
        self.assertEqual(far, shim.BULK_SEED_CEILING)
        self.assertLessEqual(near, far)

    def _modem(self):
        return types.SimpleNamespace(
            resident=0x026A, dm=self.dm,
            _bulk_seed_published=None, _bulk_seed_yielded_to=None)

    def test_service_publishes_into_the_live_and_saved_context_words(self):
        # PM 0x19e2/0x19e4 restore from DM(0x3608)/DM(0x3609) at the top of
        # every frame, so seeding only the live words survives one frame.
        self.dm[0x3FCB] = 0x01A6
        modem = self._modem()

        with contextlib.redirect_stdout(io.StringIO()):
            shim.NativeMipsModem._service_bulk_lengths(modem)

        self.assertEqual((self.dm[0x3FBC], self.dm[0x3FBD]), (0x01D7, 0x0227))
        self.assertEqual((self.dm[0x3608], self.dm[0x3609]), (0x01D7, 0x0227))

    def test_hold_tolerates_the_per_frame_decrement_without_flushing(self):
        self.dm[0x3FCB] = 0x01A6
        modem = self._modem()
        delay = shim.PortableBulkDelay()

        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(4):
                shim.NativeMipsModem._service_bulk_lengths(modem)
                self.assertTrue(delay.service(self.dm))
                # PM 0x1a13/0x1a18 write both words back one decrement low.
                self.dm[0x3FBC] -= shim.BULK_LENGTH_DECREMENT
                self.dm[0x3FBD] -= shim.BULK_LENGTH_DECREMENT

        self.assertEqual(delay._lengths, (0x01D7, 0x0227))

    def test_a_genuine_firmware_publication_wins(self):
        self.dm[0x3FCB] = 0x01A6
        modem = self._modem()
        with contextlib.redirect_stdout(io.StringIO()):
            shim.NativeMipsModem._service_bulk_lengths(modem)
            self.dm[0x3FBC], self.dm[0x3FBD] = 0x03CD, 0x041D
            shim.NativeMipsModem._service_bulk_lengths(modem)
            shim.NativeMipsModem._service_bulk_lengths(modem)

        self.assertEqual((self.dm[0x3FBC], self.dm[0x3FBD]), (0x03CD, 0x041D))
        self.assertEqual(modem._bulk_seed_yielded_to, (0x03CD, 0x041D))

    def test_only_the_two_echo_cancelling_overlays_are_seeded(self):
        self.dm[0x3FCB] = 0x01A6
        for page in (0x025F, 0x0260, 0x026D):
            with self.subTest(page=page):
                modem = self._modem()
                modem.resident = page
                shim.NativeMipsModem._service_bulk_lengths(modem)
                self.assertEqual(self.dm[0x3FBC], 0)

    def test_v34_is_seeded_as_well_as_v90d(self):
        self.dm[0x3FCB] = 0x01A6
        modem = self._modem()
        modem.resident = 0x0261

        with contextlib.redirect_stdout(io.StringIO()):
            shim.NativeMipsModem._service_bulk_lengths(modem)

        self.assertEqual(self.dm[0x3FBC], 0x01D7)


@unittest.skipIf(shim is None, "eicon_mips_shim needs unicorn")
class NegotiatedRateTests(unittest.TestCase):
    def test_successful_hardware_call_rate_words(self):
        dm = [0] * 0x4000
        dm[0x3F61] = 0x202B       # 21 + 11 bits at 8000/6 Hz
        dm[0x3F62] = 0x11E9       # V.34 flags plus speed index 9

        self.assertEqual(
            shim.v90d_negotiated_rates(dm), (42667, 7200))

    def test_v90_downstream_rate_ladder_boundaries(self):
        self.assertEqual(shim.v90_downstream_rate(0x2020), 28000)
        self.assertEqual(shim.v90_downstream_rate(0x2035), 56000)
        self.assertIsNone(shim.v90_downstream_rate(0x0009))
        self.assertIsNone(shim.v90_downstream_rate(0x002B))

    def test_v34_upstream_rate_ladder(self):
        self.assertEqual(shim.v34_rate(0x0007), 2400)
        self.assertEqual(shim.v34_rate(0x0009), 7200)
        self.assertEqual(shim.v34_rate(0x0014), 33600)
        self.assertIsNone(shim.v34_rate(0x2009))

    def test_exact_upstream_selection_captures_complete_handoff(self):
        dm = [0] * 0x4000
        dm[0x3F62] = 0x11EB       # 12,000 bit/s, capability bit 4
        dm[0x1E3F] = 0x0010
        dm[0x210B] = 0x1FFE
        dm[0x3F9B] = 4
        dm[0x204E] = 12

        self.assertEqual(
            shim.v90d_upstream_handoff(dm, dm[0x3F62]),
            (0x11EB, 4, 12))

    def test_exact_upstream_handoff_survives_transient_quality_fallback(self):
        dm = [0] * 0x4000
        dm[0x1E3F] = 0x0010       # peer permits exactly 12,000 bit/s
        dm[0x210B] = 0x1FFE
        dm[0x20BA] = 3            # final ceiling only admits through 7,200
        handoff = (0x11EB, 4, 12)

        self.assertEqual(
            shim.v90d_exact_upstream_fallback(dm, 0x11E0, handoff),
            handoff)

    def test_exact_upstream_offer_raises_ceiling_for_native_12000_setup(self):
        dm = [0] * 0x4000
        dm[0x1E3F] = 0x0010       # peer permits exactly 12,000 bit/s
        dm[0x210B] = 0x1FFE
        dm[0x20BA] = 3            # final ceiling only admits through 7,200

        self.assertEqual(
            shim.v90d_exact_upstream_ceiling_floor(dm), 5)

    def test_handoff_does_not_override_broad_or_adequate_rate_selection(self):
        dm = [0] * 0x4000
        dm[0x210B] = 0x1FFE
        handoff = (0x11EB, 4, 12)

        dm[0x1E3F] = 0x03FE       # broad peer capability mask
        dm[0x20BA] = 3
        self.assertIsNone(
            shim.v90d_exact_upstream_fallback(dm, 0x11E0, handoff))
        self.assertIsNone(
            shim.v90d_exact_upstream_ceiling_floor(dm))

        dm[0x1E3F] = 0x0010
        dm[0x20BA] = 5            # exact bit is already inside the ceiling
        self.assertIsNone(
            shim.v90d_exact_upstream_fallback(dm, 0x11E0, handoff))

    def test_service_restores_repeated_exact_12000_fallback_writes(self):
        dm = [0] * 0x4000
        dm[0x3F62] = 0x11EB
        dm[0x1E3F] = 0x0010
        dm[0x210B] = 0x1FFE
        dm[0x20BA] = 8
        dm[0x3F9B] = 4
        dm[0x204E] = 12
        modem = types.SimpleNamespace(
            resident=0x026A, dm=dm, _v90d_upstream_word=None,
            _v90d_upstream_handoff=None,
            _v90d_preserved_handoff_logged=False,
            negotiated_downstream_bps=None, negotiated_upstream_bps=None,
            _media_samples=100)

        with contextlib.redirect_stdout(io.StringIO()):
            shim.NativeMipsModem._service_negotiated_rates(modem)
            for sample in (200, 300):
                dm[0x3F62] = 0x11E0
                dm[0x3F9B] = 0
                dm[0x204E] = 3
                dm[0x20BA] = 3
                modem._media_samples = sample
                shim.NativeMipsModem._service_negotiated_rates(modem)

        self.assertEqual(dm[0x3F62], 0x11EB)
        self.assertEqual(dm[0x3F9B], 4)
        self.assertEqual(dm[0x204E], 12)
        self.assertEqual(modem.negotiated_upstream_bps, 12000)


if __name__ == "__main__":
    unittest.main()
