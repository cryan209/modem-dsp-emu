"""Keep unrelated calls and truncated visits out of hardware dwell comparisons."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from eicon_live_caller_replay import compare_dwells, hardware_events


class LiveCallerComparisonTest(unittest.TestCase):
    def test_selects_test_call_and_keeps_retrain_events(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'trace.txt'
            path.write_text('[C:2] 4:2368:984 - [P6,2] TRAINPROGRESS 00c1\n'
                            '[C:2] 4:2369:100 - [P3,1] TRAINPROGRESS 00d0\n'
                            '[C:2] 4:2369:984 - [P6,2] TRAINPROGRESS 00c3\n'
                            '[C:2] 4:2376:297 - [P6,2] TRAINPROGRESS 00c1\n')
            events = hardware_events(path, 'P6,2')
            self.assertEqual([e['trn'] for e in events], [0xc1, 0xc3, 0xc1])
            self.assertAlmostEqual(events[1]['seconds'] - events[0]['seconds'], 1)
            with self.assertRaises(ValueError):
                hardware_events(path, 'P9,1')

    def test_compares_dwell_only_when_visible_successors_match(self):
        real = [dict(trn=0xb3, seconds=10), dict(trn=0xb6, seconds=13),
                dict(trn=0xb7, seconds=14), dict(trn=0xc0, seconds=15)]
        replay = [dict(trn=0xb3, seconds=20), dict(trn=0xb3, seconds=21),
                  dict(trn=0xb6, seconds=23.01), dict(trn=0xc0, seconds=24)]
        rows = compare_dwells(real, replay)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['state'], 0xb3)
        self.assertAlmostEqual(rows[0]['difference_ms'], 10)


if __name__ == '__main__':
    unittest.main()
