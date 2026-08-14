"""The Analog card's own kernel dispatching TIKRNL.ANA off SPORT1.

These run the real emulator against the extracted build-109-789 images, so they
are slower than the pure-Python tests here, but every claim in
`tools/analog_kernel_dispatch.py` is about what the firmware does and cannot be
established without running it.

Two of them are the positive controls the module's value rests on.  The ISR
word TIKRNL claims (PM 0x0582) is a DC-removal high pass with a 1-2^-5 pole,
which the direct backend in `dial_tikrnl_drive.py` never runs because it plants
SR1 by hand instead of letting the SPORT1 ISR queue a sample.  So "the kernel
path delivers samples correctly" has to be shown at a frequency the filter
passes, and "the filter is really there" at one it does not.
"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

REPO = Path(__file__).resolve().parents[1]
IMAGES = REPO / 'artifacts/eicon-dsp/build-109-789-analog'

import dial_tikrnl_drive as drive

try:
    drive.select_firmware_set('analog109')
    import analog_kernel_dispatch as akd
except Exception as exc:                       # pragma: no cover
    akd = None
    _import_error = exc


def _signed(word):
    return word - 0x10000 if word & 0x8000 else word


@unittest.skipUnless(IMAGES.is_dir(),
                     'build-109-789 Analog images are not extracted')
@unittest.skipIf(akd is None, 'analog_kernel_dispatch did not import')
class AnalogKernelDispatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        drive.select_firmware_set('analog109')
        cls.modem = akd.AnalogKernelModem(modem_role='calling')
        cls.modem.boot()
        cls.modem.configure_modem('calling', 'pcmu')
        # The dial page asks for V.8 around sample 20, so the page walk only
        # exists once the kernel has actually been clocked.
        for index in range(200):
            value = int(3900 * math.sin(2 * math.pi * 2100 * index / 8000))
            cls.modem.frame_fast(
                cls.modem.line_rx_word(0xFF, value) & 0xFFFF, index)

    def test_kernel_registers_the_task_in_its_own_slots(self):
        """The registration is the kernel's, not the harness's."""
        card = self.modem.card
        # Kernel service 0x0017 built these two CALL instructions itself, over
        # the two words the Analog registration block names at DM 0x2E77/78.
        self.assertEqual(card.dm[0x2E77], akd.PM_FOREGROUND_SLOT)
        self.assertEqual(card.dm[0x2E78], akd.PM_ISR_SLOT)
        expected = 0x1C000F | (akd.SAMPLE_CONTINUATION << 4)
        self.assertEqual(card.pm[akd.PM_FOREGROUND_SLOT], expected)
        # And the foreground wrote its own five ring-descriptor pointers.
        self.assertEqual(card.dm[akd.DM_CMD_DESC_PTR], 0x2E47)
        self.assertEqual(card.dm[akd.DM_DOORBELL_DESC_PTR], 0x2E55)

    def test_the_card_reaches_v8_without_being_told_to(self):
        """No originate stand-in: the Analog dial page asks for page 6 itself."""
        switches = [download for _, _, download in self.modem.card.switches]
        self.assertIn(0x025F, switches, 'never loaded the V.8 page')
        self.assertLess(switches.index(0x025F), 6,
                        'V.8 arrived after an unexpected detour')
        self.assertEqual(self.modem.card.dm[akd.DM_BOOTPAGE], 6)

    def _drive_tone(self, freq, amplitude=8000, samples=2500):
        drive.select_firmware_set('analog109')
        modem = akd.AnalogKernelModem(modem_role='calling')
        modem.boot()
        modem.configure_modem('calling', 'pcmu')
        stimulus = [int(amplitude * math.sin(2 * math.pi * freq * i / 8000))
                    for i in range(samples)]
        slot = []
        for index, linear in enumerate(stimulus):
            word = modem.line_rx_word(0xFF, linear)
            modem.frame_fast(word & 0xFFFF, index)
            slot.append(_signed(modem.dm[0x3763]))
        return stimulus, slot

    @staticmethod
    def _correlate(stimulus, slot, lag, skip=400):
        num = d1 = d2 = 0.0
        for i in range(skip + lag, len(slot)):
            a, b = slot[i], stimulus[i - lag] * 0.25
            num += a * b
            d1 += a * a
            d2 += b * b
        return num / (d1 ** 0.5 * d2 ** 0.5) if d1 and d2 else 0.0

    def test_passband_sample_delivery_is_exact(self):
        """2100 Hz is well above the ISR filter's corner, so it must pass.

        PM 0x0717 scales by 0x2000 -- x0.25, the right-justified SPORT
        representation -- and the sample arrives one frame late through the
        delay slot at DM(0x31B8).
        """
        stimulus, slot = self._drive_tone(2100)
        self.assertGreater(self._correlate(stimulus, slot, lag=1), 0.99)
        self.assertGreater(max(abs(v) for v in slot[400:]), 1900)

    def test_the_isr_high_pass_attenuates_below_its_corner(self):
        """20 Hz is below the 1-2^-5 pole, so it must not pass unchanged.

        This is what distinguishes the kernel path from the direct one: the
        direct backend never runs PM 0x0582 and tracks a 20 Hz input exactly.
        """
        stimulus, slot = self._drive_tone(20)
        peak = max(abs(v) for v in slot[400:])
        self.assertLess(peak, 1500, 'a 20 Hz input was not attenuated, so the '
                                    'SPORT1 ISR filter did not run')
        self.assertLess(self._correlate(stimulus, slot, lag=1), 0.9)

    def test_rxsample_carries_live_audio_and_v8_fills_it_itself(self):
        """RXSAMPLE is filled, by the page, and the empty slots are the count.

        Commit 16f09aa read the array as unwritten -- "over a 60,001-sample
        call the receive array takes 24 distinct contents and its last two
        words stay zero" -- and concluded the kernel was meant to fill it.
        Both halves are wrong.  V8.ANA fills it itself at PM 0x173A:

            173a  CNTR = DM($3F67)        ; 4
            173b  I0 = $3F30              ; RXSAMPLE_0, as an immediate
            173d  I7 = DM($376C)          ; the 20-word resampled ring
            173e  DO $1740 UNTIL NOT CE
            173f    AX1 = DM(I7,M5)
            1740    DM(I0,M1) = AX1

        which runs 6,000 times per 24,000 line samples -- 2 kHz, four words
        each, exactly the 8 kHz stream regrouped.  RXSAMPLE_4 and _5 stay zero
        because DM(0x3F67) is 4, not because nothing writes them.  A grep for
        `DM($3F30)` misses this; the address arrives as an immediate into I0.
        """
        modem = akd.AnalogKernelModem(modem_role='calling')
        modem.boot()
        modem.configure_modem('calling', 'pcmu')
        for index in range(4000):
            value = int(8000 * math.sin(2 * math.pi * 2100 * index / 8000))
            modem.frame_fast(modem.line_rx_word(0xFF, value) & 0xFFFF, index)
        count = modem.dm[0x3F67]
        self.assertEqual(count, 4, 'the RXSAMPLE count moved; the slots this '
                                   'test calls unused are derived from it')
        used = [modem.dm[0x3F30 + k] for k in range(count)]
        signed = [v - 0x10000 if v & 0x8000 else v for v in used]
        self.assertTrue(all(signed), f'RXSAMPLE_0..{count - 1} not live: {signed}')
        self.assertGreater(max(abs(v) for v in signed), 500,
                           f'RXSAMPLE carries no signal: {signed}')
        for slot in range(count, 6):
            self.assertEqual(modem.dm[0x3F30 + slot], 0,
                             f'RXSAMPLE_{slot} is above the count and not zero')
        # And the chain that feeds it is wired end to end: the page reads the
        # word TIKRNL stores through ShellInptr (PM 0x175B, I0 = DM(0x3762)).
        self.assertEqual(modem.dm[0x3762], modem.dm[akd.DM_SHELLINPTR])


@unittest.skipUnless(IMAGES.is_dir(),
                     'build-109-789 Analog images are not extracted')
@unittest.skipIf(akd is None, 'analog_kernel_dispatch did not import')
class AnalogV8TransmitTest(unittest.TestCase):
    """Why the Analog caller's V.8 burst is an unmodulated 1083.5 Hz tone.

    PM 0x3A3C is the FSK modulator: it reads the data bit DM(0x03B3) and picks
    one of two phase increments, DM(0x03B6) or DM(0x03B7), into the phase
    accumulator DM(0x03B5).  PM 0x3A36 installs a proper V.21 pair --
    0x0FBC/0x0D11 for channel 1, 0x18AB/0x1600 for channel 2, whose ratios are
    1180/980 and 1850/1650 to four figures.  Then the CI builder at PM 0x3817
    overwrites DM(0x03B6) 38 cycles later, and both words end up 0x1156.
    """

    @staticmethod
    def _goertzel(samples, freq, rate=8000.0):
        w = 2 * math.pi * freq / rate
        c = 2 * math.cos(w)
        s1 = s2 = 0.0
        for v in samples:
            s0 = v + c * s1 - s2
            s2, s1 = s1, s0
        return s1 * s1 + s2 * s2 - c * s1 * s2

    def _burst(self, pin):
        drive.select_firmware_set('analog109')
        modem = akd.AnalogKernelModem(modem_role='calling')
        modem.boot()
        modem.configure_modem('calling', 'pcmu')
        tx = []
        for index in range(24000):
            if pin:
                modem.dm[0x03B6] = 0x0FBC
                modem.dm[0x03B7] = 0x0D11
            tx.append(modem.frame_fast(0, index))
        return modem, tx

    def test_both_increments_hold_the_v25_calling_tone(self):
        """Equal increments are correct here: this burst is the calling tone.

        0x1156 is 1300.2 Hz at the constants' own 9600 Hz calibration -- the
        V.25 calling tone -- and an unmodulated tone is what a calling modem
        is supposed to emit.  The CI builder at PM 0x3817 writing it over the
        V.21 pair is deliberate, not corruption.
        """
        modem, _ = self._burst(pin=False)
        self.assertEqual(modem.dm[0x03B6], modem.dm[0x03B7])
        self.assertEqual(modem.dm[0x03B6], 0x1156)

    def _tone_for(self, increment):
        """Wire frequency with the data bit made irrelevant."""
        drive.select_firmware_set('analog109')
        modem = akd.AnalogKernelModem(modem_role='calling')
        modem.boot()
        modem.configure_modem('calling', 'pcmu')
        tx = []
        for index in range(16000):
            modem.dm[0x03B6] = increment
            modem.dm[0x03B7] = increment
            tx.append(modem.frame_fast(0, index))
        best = None
        for start in range(0, len(tx) - 4800, 800):
            window = tx[start:start + 4800]
            energy = sum(v * v for v in window)
            if best is None or energy > best[1]:
                best = (start, energy)
            window = tx[best[0]:best[0] + 4800]
        window = tx[best[0]:best[0] + 4800]
        rms = (sum(v * v for v in window) / len(window)) ** 0.5
        coarse = max((f / 10.0 for f in range(4000, 20000, 25)),
                     key=lambda f: self._goertzel(window, f))
        fine = max((f / 10.0 for f in range(int((coarse - 4) * 10),
                                            int((coarse + 4) * 10) + 1)),
                   key=lambda f: self._goertzel(window, f))
        return fine, rms

    def test_the_tone_constants_are_calibrated_for_9600_not_8000(self):
        """The single root cause: every tone comes out at 5/6 of nominal.

        Forcing both increments to one value makes the output a pure tone, so
        increment -> frequency can be read off directly.  Measured, the phase
        accumulator advances exactly 32768 counts per 8000 Hz:

            0x0A00 ->  625.0 Hz    0x0D11 -> 816.7 Hz
            0x0FBC ->  983.4 Hz    0x1156 -> 1083.5 Hz

        all giving 4.0960 counts/Hz.  But at 9600 Hz the same five constants
        the page carries land on standard tones to within 0.015%:
        0x0D11 = 980, 0x0FBC = 1180 (V.21 channel 1), 0x1600 = 1650,
        0x18AB = 1850 (channel 2), 0x1156 = 1300 (V.25 calling tone).  Five
        exact hits is not a coincidence, so the constants are 9600 Hz
        constants and the chain clocks them at 8000.
        """
        scale = 32768 / 8000.0
        for increment in (0x0A00, 0x0D11, 0x0FBC, 0x1156):
            tone, rms = self._tone_for(increment)
            self.assertGreater(rms, 20, f'0x{increment:04x} produced no tone')
            self.assertAlmostEqual(
                increment / tone, scale, delta=0.01,
                msg=f'0x{increment:04x} gave {tone} Hz; the 8000 Hz '
                    'accumulator calibration would be withdrawn')
            # and the same constant is a standard tone at 9600
            nominal = increment / (32768 / 9600.0)
            self.assertLess(min(abs(nominal - s)
                                for s in (980, 1180, 1650, 1850, 1300, 750)),
                            1.0, f'0x{increment:04x} is {nominal:.1f} Hz at '
                                 '9600 and matches no standard tone')


@unittest.skipUnless(IMAGES.is_dir(),
                     'build-109-789 Analog images are not extracted')
@unittest.skipIf(akd is None, 'analog_kernel_dispatch did not import')
class AnalogV8RateBlockTest(unittest.TestCase):
    """Where the transmit rate comes from, and what it cannot reach.

    PM 0x167A copies a 10-word parameter block into DM(0x3754).. selected by
    DM(0x3F66) from the pointer table at 0x37C3.  PM 0x16C4 then builds the
    resampler tables from it by Bresenham, ratio DM(0x3754)/DM(0x3755), and
    PM 0x1771 walks them.  V.8's own init hardcodes DM(0x3F66) = 4 at
    PM 0x3655/0x3656 -- the 15/15 identity -- over the 8 DIAL had selected.
    """

    BLOCKS = {0: (15, 20), 3: (15, 16), 4: (15, 15), 5: (15, 14),
              6: (15, 12), 7: (15, 10), 8: (15, 18)}

    @staticmethod
    def _immediate_ar(value):
        """Encode `AR = $value`, as the page's own init at PM 0x3655 does."""
        return 0x400000 | ((value & 0xFFFF) << 4) | 0x0A

    def _with_selector(self, index):
        drive.select_firmware_set('analog109')
        modem = akd.AnalogKernelModem(modem_role='calling')
        original = modem.card.download_overlay

        def patched(download_id):
            result = original(download_id)
            if download_id == 0x025F:
                modem.pm[0x3655] = self._immediate_ar(index)
            return result

        modem.card.download_overlay = patched
        modem.boot()
        modem.configure_modem('calling', 'pcmu')
        for sample in range(12000):
            modem.frame_fast(0, sample)
        return modem

    def test_the_selector_chooses_the_documented_rate_blocks(self):
        for index, (numerator, denominator) in self.BLOCKS.items():
            modem = self._with_selector(index)
            self.assertEqual((modem.dm[0x3754], modem.dm[0x3755]),
                             (numerator, denominator),
                             f'DM(0x3F66) = {index} did not select its block')

    def test_no_rate_block_reaches_the_9600_the_tones_are_cut_for(self):
        """The negative that moves the question off the resampler.

        Transmit frequency is 1083.5 Hz x DM(0x3754)/DM(0x3755) -- measured
        1160.9 at 15/14 and 1354.4 at 15/12, both exact.  The tone constants
        are V.21/V.25 standards at 9600 Hz, which needs a ratio of 1.2, and
        every block ships DM(0x3754) = 15, so 1.2 would need DM(0x3755) = 12.5.
        No block provides it, and DM(0x3F67) does not affect frequency at all.
        So 9600 cannot come from rate selection; if the constants are right it
        has to be the codec rate itself, which this firmware never programs --
        SPORT1's SCLKDIV and RFSDIV are both zero.
        """
        ratios = {n / d for n, d in self.BLOCKS.values()}
        self.assertNotIn(1.2, ratios)
        self.assertTrue(all(abs(r - 1.2) > 0.04 for r in ratios),
                        f'a block now reaches 1.2: {sorted(ratios)}')
        self.assertTrue(all(n == 15 for n, _ in self.BLOCKS.values()))


if __name__ == '__main__':
    unittest.main()
