"""The log cap is an instrument, so it is known good before a call needs it.

The failure it exists to catch is a line that fires per media tick, which is
why the cases below are about a *site* rather than a message: the runaway that
prompted this formatted a different value each time and would have escaped any
cap keyed on the text.
"""
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))

import logcap


class LogCapTests(unittest.TestCase):

    def setUp(self):
        logcap.reset()
        self.addCleanup(logcap.reset)

    def _emit_many(self, count, limit=3):
        """One call site, `count` times, each with different text."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            for i in range(count):
                logcap.emit(f'[test] near={i} far={i * 2}', limit=limit)
        return buf.getvalue().splitlines()

    def test_a_site_stops_printing_at_its_limit(self):
        lines = self._emit_many(50, limit=3)
        payload = [ln for ln in lines if ln.startswith('[test]')]
        self.assertEqual(len(payload), 3)
        # Varying the formatted value must not buy more budget -- that is
        # exactly what the bulk-delay line did.
        self.assertEqual(payload[0], '[test] near=0 far=0')
        self.assertEqual(payload[-1], '[test] near=2 far=4')

    def test_reaching_the_limit_says_so_once(self):
        lines = self._emit_many(50, limit=3)
        notes = [ln for ln in lines if ln.startswith('[logcap]')]
        self.assertEqual(len(notes), 1)
        self.assertIn('reached 3 lines', notes[0])
        self.assertIn('test_logcap.py:', notes[0])

    def test_emit_reports_whether_it_printed(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            first = logcap.emit('one', limit=1)
            second = logcap.emit('two', limit=1)
        self.assertTrue(first)
        # The second call is a different site, so it gets its own budget.
        self.assertTrue(second)

    def test_two_sites_do_not_share_a_budget(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            for _ in range(10):
                logcap.emit('site A', limit=2)
            for _ in range(10):
                logcap.emit('site B', limit=2)
        printed = [ln for ln in buf.getvalue().splitlines()
                   if not ln.startswith('[logcap]')]
        self.assertEqual(printed.count('site A'), 2)
        self.assertEqual(printed.count('site B'), 2)

    def test_summary_names_the_runaway_and_the_count(self):
        self._emit_many(50, limit=3)
        summary = logcap.summary()
        self.assertIn('1 site(s) capped', summary)
        # 50 attempts against a limit of 3 leaves 47 unprinted.
        self.assertIn('47 lines suppressed', summary)
        self.assertIn('test_logcap.py:', summary)

    def test_a_quiet_run_reports_nothing_to_report(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            logcap.emit('once', limit=200)
        self.assertEqual(logcap.throttled(), [])
        self.assertEqual(logcap.summary(), '[logcap] no site hit the cap')

    def test_throttled_ranks_the_worst_site_first(self):
        # throttled() reports against the module default, so drive past it.
        buf = io.StringIO()
        with redirect_stdout(buf):
            for _ in range(logcap.DEFAULT_LIMIT + 5):
                logcap.emit('modest')
            for _ in range(logcap.DEFAULT_LIMIT + 500):
                logcap.emit('runaway')
        rows = logcap.throttled()
        self.assertEqual(len(rows), 2)
        self.assertEqual([row[2] for row in rows], [500, 5])


if __name__ == '__main__':
    unittest.main()
