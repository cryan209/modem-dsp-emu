"""Integrity patterns must survive arbitrary byte chunking and real PTY flow."""
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time
import tty
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from eicon_stream_integrity import Pattern


class StreamIntegrityTest(unittest.TestCase):
    def test_pattern_is_independent_of_chunk_boundaries(self):
        pattern = Pattern('boundary-control')
        whole = pattern.at(0, 50000)
        self.assertEqual(whole, b''.join(pattern.at(i, min(173, 50000 - i))
                                        for i in range(0, 50000, 173)))
        self.assertNotEqual(whole[:100], Pattern('other-direction').at(0, 100))

    def test_real_pty_duplex_bytes_cross_multiple_pattern_blocks(self):
        script = Path(__file__).resolve().parents[1] / 'tools/eicon_stream_integrity.py'
        master, slave = pty.openpty()
        tty.setraw(slave)
        os.set_blocking(master, False)
        size = 40000
        sent = Pattern('eicon-integrity-v1:unit:serial').at(0, size)
        expected = Pattern('eicon-integrity-v1:unit:pty').at(0, size)
        process = None
        try:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'result.json'
                process = subprocess.Popen([sys.executable, str(script), '--mode', 'pty',
                    '--device', os.ttyname(slave), '--seed', 'unit', '--size', str(size),
                    '--timeout', '5', '--result', str(path)], stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True)
                self.assertEqual(json.loads(process.stdout.readline())['event'], 'armed')
                rx = bytearray()
                tx = 0
                deadline = time.monotonic() + 5
                while (tx < size or len(rx) < size) and time.monotonic() < deadline:
                    r, w, _ = select.select([master], [master] if tx < size else [], [], .05)
                    if w:
                        tx += os.write(master, sent[tx:tx + 233])
                    if r:
                        rx.extend(os.read(master, 4096))
                process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0)
                self.assertEqual(bytes(rx), expected)
                result = json.loads(path.read_text())
                self.assertTrue(result['passed'])
                self.assertEqual(result['rx_sha256'], hashlib.sha256(sent).hexdigest())
        finally:
            if process and process.poll() is None:
                process.kill()
                process.communicate()
            os.close(master)
            os.close(slave)

    def test_duration_stop_drains_exact_final_counts(self):
        script = Path(__file__).resolve().parents[1] / 'tools/eicon_stream_integrity.py'
        master, slave = pty.openpty()
        tty.setraw(slave)
        os.set_blocking(master, False)
        process = None
        try:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'result.json'
                process = subprocess.Popen([sys.executable, str(script), '--mode', 'pty',
                    '--device', os.ttyname(slave), '--seed', 'stop', '--size', str(2**40),
                    '--duration', '.3', '--timeout', '5', '--result', str(path)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True)
                self.assertEqual(json.loads(process.stdout.readline())['event'], 'armed')
                process.stdin.write('START\n')
                process.stdin.flush()
                source = Pattern('eicon-integrity-v1:stop:serial')
                tx = 0
                rx = bytearray()
                stop_at = time.monotonic() + .3
                deadline = stop_at + 5
                sent_limit = False
                while time.monotonic() < deadline:
                    if not sent_limit and time.monotonic() >= stop_at:
                        process.stdin.write(f'RX_LIMIT {tx}\n')
                        process.stdin.flush()
                        sent_limit = True
                    r, w, _ = select.select([master], [] if sent_limit else [master], [], .01)
                    if w:
                        tx += os.write(master, source.at(tx, 233))
                    if r:
                        rx.extend(os.read(master, 4096))
                    if path.exists() and len(rx) == json.loads(path.read_text())['tx']:
                        break
                process.communicate(timeout=5)
                result = json.loads(path.read_text())
                self.assertEqual(process.returncode, 0)
                self.assertTrue(result['passed'])
                self.assertEqual(result['rx'], tx)
                self.assertGreater(tx, 0)
                self.assertEqual(bytes(rx), Pattern('eicon-integrity-v1:stop:pty').at(0, len(rx)))
        finally:
            if process and process.poll() is None:
                process.kill()
                process.communicate()
            os.close(master)
            os.close(slave)


if __name__ == '__main__':
    unittest.main()
