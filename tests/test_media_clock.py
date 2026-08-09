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
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))

from eicon_adsp_sip import Call, EiconSipEndpoint, TICK_SECONDS


def endpoint(**kwargs):
    """Just the attributes rx_ready() and next_wakeup() actually read."""
    state = SimpleNamespace(rx_prefill_samples=1600, rx_hold_seconds=0.5,
                            rx_guard_samples=0, rx_drain_samples=8000,
                            realtime=False, pty=None)
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
