"""The media clock: what paces the RTP stream, and what quietly loses time.

The transmit stream of a live call measured -1240 ppm against a receive stream
at -5, and the wire said where it went. Two things came out of that capture and
both are asserted here.

The first is that the loop has almost no headroom: when it is behind it runs
quanta back to back, and those recovery quanta measured a median 17.3 ms for
20 ms of media. Three milliseconds is all there is to repay a stall with, so a
73 ms gap takes half a second to work off. That is not a bug to fix in a test,
but it is the reason the second thing matters -- with headroom that thin,
anything that forgives a deficit instead of working it off is most of the
error. `tick_cost` is what makes it visible; nothing reported it before, and a
worst-tick figure with a count over an 18 ms budget cannot tell 3 ms of
headroom from 15.

The second is the clock hold. Waiting for a late packet is right -- inventing
silence for the far modem to measure is not recoverable -- but the wait used to
be taken by moving the media schedule to `now`, which threw away however far
behind that schedule already was.
"""
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))

from eicon_adsp_sip import Call, EiconSipEndpoint, TICK_SECONDS


def endpoint(**kwargs):
    """Just the attributes rx_ready() and next_wakeup() actually read."""
    state = SimpleNamespace(rx_prefill_samples=1600, rx_hold_seconds=0.5,
                            rx_guard_samples=0, rx_drain_samples=8000,
                            realtime=False, pty=None, tx_target_quanta=0)
    state.wants_quantum = lambda call, now: EiconSipEndpoint.wants_quantum(
        state, call, now)
    for name, value in kwargs.items():
        setattr(state, name, value)
    return state


def call(**kwargs):
    state = Call(sip_peer=('192.0.2.1', 5060), rtp_peer=('192.0.2.1', 4000),
                 call_id='test', local_tag='tag', card=None)
    state.rx_started = True
    state.samples = 80000
    state.packets = 5
    for name, value in kwargs.items():
        setattr(state, name, value)
    return state


class ClockHoldTests(unittest.TestCase):

    def held_call(self, now=100.0, behind=0.05):
        """A call whose queue has run dry, already `behind` seconds late."""
        return call(next_tick=now - behind)

    def test_a_hold_does_not_move_the_media_schedule(self):
        state, now = endpoint(), 100.0
        current = self.held_call(now)
        scheduled = current.next_tick
        self.assertFalse(EiconSipEndpoint.rx_ready(state, current, now))
        self.assertEqual(current.next_tick, scheduled)
        self.assertEqual(current.rx_holds, 1)

    def test_the_owed_quanta_are_still_owed_when_the_packet_arrives(self):
        """50 ms late is two and a half quanta the loop has to work off. The
        samples behind them are real received audio, not time to forgive."""
        state, now = endpoint(), 100.0
        current = self.held_call(now, behind=0.05)
        EiconSipEndpoint.rx_ready(state, current, now)
        current.rx.extend([0] * 1600)           # the packet turns up
        self.assertTrue(EiconSipEndpoint.rx_ready(state, current, now + 0.002))
        self.assertLessEqual(current.next_tick, now - 0.05 + 1e-9)

    def test_the_retry_is_what_wakes_the_selector_not_the_schedule(self):
        state, now = endpoint(), 100.0
        current = self.held_call(now, behind=0.0)
        current.next_tick = now + TICK_SECONDS
        EiconSipEndpoint.rx_ready(state, current, now)
        state.call = current
        self.assertAlmostEqual(EiconSipEndpoint.next_wakeup(state, now),
                               0.002, places=6)

    def test_a_schedule_in_the_past_wakes_immediately(self):
        state = endpoint()
        state.call = call(next_tick=99.9)
        self.assertEqual(EiconSipEndpoint.next_wakeup(state, 100.0), 0.0)

    def test_a_stale_retry_does_not_shorten_a_later_wake_up(self):
        state = endpoint()
        state.call = call(next_tick=100.02, rx_retry_at=99.5)
        self.assertAlmostEqual(EiconSipEndpoint.next_wakeup(state, 100.0),
                               0.02, places=6)

    def test_a_backlog_still_beats_both(self):
        state = endpoint()
        state.call = call(next_tick=100.02, rx_retry_at=100.01)
        state.call.rx.extend([0] * 9000)
        self.assertEqual(EiconSipEndpoint.next_wakeup(state, 100.0), 0.0)

    def test_a_dry_queue_past_the_hold_runs_on_silence(self):
        """The hold is bounded: a peer that has genuinely stopped must not
        stall the call for ever."""
        state, now = endpoint(), 100.0
        current = self.held_call(now)
        EiconSipEndpoint.rx_ready(state, current, now)
        self.assertTrue(EiconSipEndpoint.rx_ready(state, current, now + 1.0))


class WireClockTests(unittest.TestCase):
    """The wire clock is not the emulator's clock.

    A quantum takes a median 17 ms to produce and occasionally stalls for 70 to
    100, and each one used to go out at the moment it was produced -- so the
    far modem demodulated every one of those stalls. With a cushion between the
    two, `next_send` accumulates absolutely and the wire sees 8000 Hz whatever
    the emulator is doing.
    """

    def endpoint(self, target=5):
        state = endpoint(tx_target_quanta=target)
        state.sent = []
        state.transmit_one = lambda call: (
            state.sent.append(call.tx_queue.popleft()))
        state.service_transmit = lambda call, now: (
            EiconSipEndpoint.service_transmit(state, call, now))
        return state

    def primed(self, state, now=100.0):
        """A call whose cushion is full and whose wire clock has started."""
        current = call()
        current.tx_queue.extend([b'q'] * state.tx_target_quanta)
        state.service_transmit(current, now)
        return current

    def test_the_cushion_fills_before_anything_goes_out(self):
        state = self.endpoint()
        current = call()
        current.tx_queue.extend([b'q'] * (state.tx_target_quanta - 1))
        state.service_transmit(current, 100.0)
        self.assertEqual(state.sent, [])
        self.assertEqual(current.next_send, 0.0)

    def test_the_schedule_is_absolute_once_it_starts(self):
        state = self.endpoint()
        current = self.primed(state)
        self.assertEqual(len(state.sent), 1)        # the priming quantum
        for step in range(1, 4):
            current.tx_queue.append(b'more')
            state.service_transmit(current, 100.0 + step * TICK_SECONDS)
        self.assertEqual(len(state.sent), 4)
        self.assertAlmostEqual(current.next_send, 100.0 + 4 * TICK_SECONDS)

    def test_a_stalled_emulator_does_not_stall_the_wire(self):
        """The case this exists for: the pump produces nothing for 70 ms and
        the far end still gets a packet every 20."""
        state = self.endpoint()
        current = self.primed(state)
        before = len(state.sent)
        for step in range(1, 4):                    # 60 ms, nothing produced
            state.service_transmit(current, 100.0 + step * TICK_SECONDS)
        self.assertEqual(len(state.sent) - before, 3)
        self.assertFalse(current.tx_underruns)

    def test_a_burst_of_production_still_leaves_the_wire_at_20_ms(self):
        state = self.endpoint()
        current = self.primed(state)
        current.tx_queue.extend([b'burst'] * 10)    # the pump catches up
        state.service_transmit(current, 100.0 + TICK_SECONDS)
        # One tick has passed, so exactly one more quantum went out; the other
        # nine are still waiting their turn rather than being flushed.
        self.assertEqual(len(state.sent), 2)
        self.assertEqual(len(current.tx_queue), 13)

    def test_an_exhausted_cushion_slips_rather_than_inventing_silence(self):
        """A hole in the middle of what a modem is measuring is not
        recoverable, so late audio wins over invented audio -- and the slip is
        counted, because it is the emulator falling behind."""
        state = self.endpoint()
        current = self.primed(state)
        current.tx_queue.clear()
        state.service_transmit(current, 100.0 + 3 * TICK_SECONDS)
        self.assertEqual(current.tx_underruns, 1)
        self.assertAlmostEqual(current.next_send, 100.0 + 3 * TICK_SECONDS)
        current.tx_queue.append(b'late')
        state.service_transmit(current, 100.0 + 3 * TICK_SECONDS)
        self.assertEqual(state.sent[-1], b'late')

    def test_production_is_gated_by_the_queue_not_the_clock(self):
        state = self.endpoint()
        current = call(next_tick=1e9)               # the wall clock says no
        self.assertTrue(EiconSipEndpoint.wants_quantum(state, current, 100.0))
        current.tx_queue.extend([b'q'] * state.tx_target_quanta)
        self.assertFalse(EiconSipEndpoint.wants_quantum(state, current, 100.0))

    def test_with_the_buffer_off_nothing_is_scheduled_at_all(self):
        state = self.endpoint(target=0)
        current = call()
        current.tx_queue.append(b'q')
        state.service_transmit(current, 100.0)
        self.assertEqual(state.sent, [])            # _queue_rtp sent it inline
        self.assertEqual(current.next_send, 0.0)

    def test_the_wire_clock_is_never_slept_through(self):
        state = self.endpoint()
        state.call = self.primed(state)
        state.call.tx_queue.extend([b'q'] * state.tx_target_quanta)
        wake = EiconSipEndpoint.next_wakeup(state, 100.0)
        self.assertGreater(wake, 0.0)
        self.assertLessEqual(wake, TICK_SECONDS)

    def test_room_in_the_cushion_is_work_to_do_now(self):
        state = self.endpoint()
        state.call = call()
        self.assertEqual(EiconSipEndpoint.next_wakeup(state, 100.0), 0.0)


class WireClockThreadTests(unittest.TestCase):
    """The clock has to keep time while the emulator holds the interpreter.

    A queue on the producing thread decouples nothing: the pump holds it for
    the whole of a 70 ms stall, so the sender cannot send during one however
    deep the cushion is. This is the part of the claim that only a real thread
    and a real stall can check.
    """

    def endpoint(self):
        state = SimpleNamespace(tx_target_quanta=3, sent=[])
        state.transmit_one = lambda call: (
            call.tx_queue.popleft(),
            state.sent.append(time.monotonic()))
        for name in ('service_transmit', 'start_transmit_clock',
                     'stop_transmit_clock', '_transmit_loop'):
            setattr(state, name, getattr(EiconSipEndpoint, name).__get__(state))
        return state

    def test_the_wire_keeps_its_schedule_through_a_stall(self):
        state = self.endpoint()
        current = call()
        current.tx_queue.extend([b'q'] * 6)
        state.start_transmit_clock(current)
        self.addCleanup(state.stop_transmit_clock, current)
        # The producing thread disappears into the emulator for 80 ms, which
        # is what the captures show, then tops the queue back up.
        for _ in range(4):
            time.sleep(0.080)
            current.tx_queue.extend([b'q'] * 4)
        state.stop_transmit_clock(current)
        gaps = [(b - a) * 1000
                for a, b in zip(state.sent, state.sent[1:])]
        self.assertGreater(len(state.sent), 10)
        # Nothing like an 80 ms hole: the bound is the interpreter's switch
        # interval, not the stall.
        self.assertLess(max(gaps), 45.0, f'gaps were {gaps}')
        self.assertFalse(current.tx_underruns)

    def test_stopping_it_twice_is_harmless(self):
        state = self.endpoint()
        current = call()
        state.start_transmit_clock(current)
        state.stop_transmit_clock(current)
        state.stop_transmit_clock(current)
        self.assertIsNone(current.tx_thread)

    def test_it_does_not_start_when_the_buffer_is_off(self):
        state = self.endpoint()
        state.tx_target_quanta = 0
        current = call()
        state.start_transmit_clock(current)
        self.assertIsNone(current.tx_thread)


class TickCostTests(unittest.TestCase):

    def test_nothing_measured_says_so(self):
        self.assertEqual(EiconSipEndpoint.tick_cost(call()), 'tick cost n/a')

    def test_the_headroom_is_what_is_left_of_the_quantum(self):
        current = call(tick_seconds=1.73, tick_count=100,
                       pump_seconds=1.40, diag_seconds=0.05,
                       send_seconds=0.10, link_seconds=0.18)
        current.tick_histogram[8] = 95          # 16..18 ms
        current.tick_histogram[12] = 5
        line = EiconSipEndpoint.tick_cost(current)
        self.assertIn('mean 17.3 ms', line)
        self.assertIn('2.7 ms headroom', line)
        self.assertIn('p95 <18 ms', line)
        self.assertIn('81% pump', line)

    def test_an_idle_rig_reports_its_headroom_too(self):
        current = call(tick_seconds=0.40, tick_count=100,
                       pump_seconds=0.30, diag_seconds=0.02,
                       send_seconds=0.04, link_seconds=0.04)
        current.tick_histogram[2] = 100
        self.assertIn('16.0 ms headroom', EiconSipEndpoint.tick_cost(current))


if __name__ == '__main__':
    unittest.main()


class DiagnosticHeaderTests(unittest.TestCase):
    """The CSV header and the row it describes must stay the same length.

    Nothing enforced this, and the failure is silent: add a word to one and
    every column after it is read as its neighbour. The archive is analysed by
    column name, so a shift would be believed rather than noticed.
    """

    def test_the_header_names_every_value_and_no_more(self):
        import ctypes
        import tempfile
        from types import SimpleNamespace as NS
        from eicon_adsp_sip import RtpCapture
        with tempfile.TemporaryDirectory() as directory:
            capture = RtpCapture(Path(directory) / 'check', 'pcmu')
            buf = (ctypes.c_uint16 * 0x4000)()
            dm = ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint16))
            capture.write_diag(160, NS(dm=dm, resident=0x026A))
            capture.diag.flush()
            lines = (Path(directory) / 'check.adsp.csv').read_text().splitlines()
        header, row = lines[0].split(','), lines[1].split(',')
        self.assertEqual(len(header), len(row))
        self.assertEqual(header[0], 'sample')
        self.assertEqual(header[-1], 'upstream_local_mask')


class ReceiveHealthTests(unittest.TestCase):
    """What the modem is handed, and what it made of it, on every call.

    The upstream rate ladder is set by a quality word that only ever lands in
    five bands, and the receiver's own SNRatio says why far more sharply than
    a level does: across one call's 21,454 data-state records, RxLevel moving
    seven units took SNRatio from 36.5 dB to 13.5 dB. All of it was already in
    the capture and none of it was reported, which is why it took the rate
    ladder to make anyone look.
    """

    def call_with(self, dm_words=()):
        import ctypes
        from types import SimpleNamespace as NS
        from eicon_adsp_sip import Call
        buf = (ctypes.c_uint16 * 0x4000)()
        dm = ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint16))
        for address, value in dict(dm_words).items():
            dm[address] = value
        current = Call(sip_peer=0, rtp_peer=0, call_id='x', local_tag='y',
                       card=NS(dm=dm))
        current.samples = 80000
        return current

    def report(self, current):
        import contextlib
        import io
        from eicon_adsp_sip import EiconSipEndpoint
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            EiconSipEndpoint.receive_health(None, current)
        return out.getvalue().strip()

    def test_the_level_and_the_receivers_verdict_are_reported_together(self):
        current = self.call_with({0x3F7D: 0x39, 0x3F78: 0x22, 0x3F86: 1,
                                  0x0FCF: 0x42, 0x20BA: 12})
        current.rx_energy = (2140 ** 2) * 8000
        current.rx_energy_samples = 8000
        current.rx_peak_high = 7932
        line = self.report(current)
        self.assertIn('SNRatio 36.5 dB', line)          # half-dB from 8 dB
        self.assertIn('ceiling 28800 bit/s', line)
        self.assertIn('-17.3 dBm0', line)

    def test_the_accumulator_is_cleared_so_seconds_do_not_smear(self):
        current = self.call_with({0x3F7D: 0})
        current.rx_energy = (2140 ** 2) * 8000
        current.rx_energy_samples = 8000
        current.rx_peak_high = 7932
        self.report(current)
        self.assertEqual(current.rx_energy, 0)
        self.assertEqual(current.rx_peak_high, 0)

    def test_a_silent_second_is_not_a_division_by_zero(self):
        current = self.call_with()
        current.rx_energy_samples = 8000
        self.assertIn('dBm0', self.report(current))

    def test_nothing_measured_yet_reports_nothing(self):
        self.assertEqual(self.report(self.call_with()), '')
