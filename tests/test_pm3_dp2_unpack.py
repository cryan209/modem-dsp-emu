"""Regression checks for the ComOS IDMA record stream."""
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import pm3_dp2_unpack


class Dp2RecordsTest(unittest.TestCase):
    def test_exact_lengths_pm_dm_and_release_record(self):
        path = ROOT / "artifacts/pm3-comos/3.9.1/7_m2c_2.2.dp2.bin"
        if not path.exists():
            self.skipTest("extracted ComOS artifact is not present")
        records = list(pm3_dp2_unpack.records(path.read_bytes()))
        self.assertEqual((records[0][0], records[0][1], len(records[0][2])),
                         (0x0030, 0x01B0, 0x00D8))
        self.assertEqual(records[0][0] + len(records[0][2]), 0x0108)
        self.assertTrue(any(address & 0x4000 for address, _, _ in records))
        self.assertEqual(records[-1], (0, 2, [0x1FBBAF]))
        self.assertEqual(sum(count for _, count, _ in records), 36578)

    def test_rejects_truncated_record(self):
        with self.assertRaisesRegex(ValueError, "overruns"):
            list(pm3_dp2_unpack.records(bytes.fromhex("300004000100")))


if __name__ == "__main__":
    unittest.main()
