import importlib.util
from pathlib import Path
import unittest
from io import BytesIO
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('radio', Path(__file__).resolve().parents[1]/'deploy/isp-radio/radio.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class RadioTests(unittest.TestCase):
    def handler(self, path, address='10.0.0.1'):
        h = object.__new__(r.Handler)
        h.path = path
        h.client_address = (address, 1234)
        h.wfile = BytesIO()
        h.headers_for = Mock()
        h.send_error = Mock()
        return h

    def test_only_real_codec_rates(self):
        for rate in r.RATES:
            self.assertEqual(r.profile('/cheese/%s.mp3' % rate), (rate, 'mp3'))
        for path in ('/cheese/8.wma', '/cheese/16.wma', '/cheese/16.rm', '/cheese/64.rm', '/cheese/128.mp3', '/cheese/8.mp3/../../etc/passwd'):
            with self.assertRaises(ValueError):
                r.profile(path)
        self.assertEqual(r.profile('/cheese/8.rm'), (8, 'rm'))

    def test_playlists_head_and_acl_do_not_start_encoder(self):
        with patch.object(r.subprocess, 'Popen') as launch:
            for kind, ext in (('mp3', 'm3u'), ('wma', 'asx'), ('rm', 'ram')):
                rate = 24 if kind == 'wma' else 8
                path = '/cheese/%s.%s' % (rate, kind)
                h = self.handler(path + '.' + ext)
                h.do_GET()
                self.assertIn((r.BASE + path).encode(), h.wfile.getvalue())
                h.headers_for.assert_called_once()
                self.handler(path).do_GET(head=True)
            h = self.handler('/cheese/8.mp3', '8.8.8.8')
            h.do_GET()
            h.send_error.assert_called_once_with(403)
            launch.assert_not_called()

    def test_source_is_fixed_and_verified(self):
        cmd = r.command(16, 'mp3')
        self.assertEqual(cmd[cmd.index('-i')+1], r.SOURCE)
        self.assertEqual(cmd[cmd.index('-tls_verify')+1], '1')

    def test_full_and_failed_launch_cleanup(self):
        try:
            r.LISTENERS['10.0.0.1'] = 2
            h = self.handler('/cheese/8.mp3')
            with patch.object(r.subprocess, 'Popen') as launch:
                h.do_GET()
                launch.assert_not_called()
            self.assertEqual(h.send_error.call_args.args[0], 503)
            r.LISTENERS.clear()
            with patch.object(r.subprocess, 'Popen', side_effect=OSError):
                self.handler('/cheese/8.mp3').do_GET()
            self.assertFalse(r.LISTENERS)
        finally:
            r.LISTENERS.clear()

    def test_realaudio_timestamp_repair_across_fragmented_reads(self):
        header = b'.RMF' + (18).to_bytes(4, 'big') + bytes(10)
        data_header = b'DATA' + (18).to_bytes(4, 'big') + bytes(10)
        packet = bytes(2) + (32).to_bytes(2, 'big') + bytes(28)
        wire = header + data_header + packet * 3
        timing = r.RealAudioTiming()
        output = b''.join(timing.feed(wire[i:i+7]) for i in range(0, len(wire), 7))
        self.assertEqual(output[:36], header + data_header)
        self.assertEqual([int.from_bytes(output[36+i*32+6:36+i*32+10], 'big') for i in range(3)], [0,20,40])
        self.assertFalse(timing.buffer)
