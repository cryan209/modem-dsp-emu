import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "compare_execution_history.py"


class ExecutionHistoryCompareTests(unittest.TestCase):
    def _write(self, directory, name, rows):
        path = Path(directory) / name
        fields = ["sample", "resident", "entry_pc", "call_0703"]
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def _run(self, left, right):
        return subprocess.run(
            [sys.executable, str(TOOL), str(left), str(right)],
            text=True, capture_output=True, check=False)

    def test_reports_first_field_divergence(self):
        with tempfile.TemporaryDirectory() as directory:
            left = self._write(directory, "left.csv", [
                {"sample": "1", "resident": "618", "entry_pc": "10", "call_0703": "1"},
                {"sample": "2", "resident": "618", "entry_pc": "11", "call_0703": "1"},
            ])
            right = self._write(directory, "right.csv", [
                {"sample": "1", "resident": "618", "entry_pc": "10", "call_0703": "1"},
                {"sample": "2", "resident": "618", "entry_pc": "12", "call_0703": "1"},
            ])
            result = self._run(left, right)
            self.assertEqual(result.returncode, 1)
            self.assertIn("row 1, sample 2, field entry_pc", result.stdout)

    def test_accepts_identical_histories(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [{"sample": "1", "resident": "618", "entry_pc": "10", "call_0703": "1"}]
            left = self._write(directory, "left.csv", rows)
            right = self._write(directory, "right.csv", rows)
            result = self._run(left, right)
            self.assertEqual(result.returncode, 0)
            self.assertIn("identical: 1 rows", result.stdout)


if __name__ == "__main__":
    unittest.main()
