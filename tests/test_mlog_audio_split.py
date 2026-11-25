"""Trace law labels must never silently transcode or mix B channels."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class MlogAudioSplitTest(unittest.TestCase):
    def test_laws_preserve_channel_bytes(self):
        script = Path(__file__).resolve().parents[1] / 'tools/mlog_audio_split.py'
        for law, suffix in [('pcma', 'alaw'), ('pcmu', 'ulaw')]:
            with self.subTest(law=law), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                trace = root / 'trace.txt'
                trace.write_text('[C:2] 0:0000:000 - Audio1 (4)\n'
                                 '  (0000) - [*,1] SAMPLE[] D555 807F\n'
                                 '[C:2] 0:0000:000 - Audio2 (4)\n'
                                 '  (0000) - [*,2] SAMPLE[] 1234 ABCD\n')
                subprocess.run([sys.executable, str(script), str(trace),
                                str(root / 'out'), '--caller', '2',
                                '--answerer', '1', '--law', law],
                               check=True, capture_output=True)
                expected = {'caller.rx': b'\x12\xab', 'caller.tx': b'\x34\xcd',
                            'answerer.rx': b'\xd5\x80', 'answerer.tx': b'\x55\x7f'}
                for name, data in expected.items():
                    self.assertEqual((root / 'out' / f'{name}.{suffix}').read_bytes(), data)


if __name__ == '__main__':
    unittest.main()
