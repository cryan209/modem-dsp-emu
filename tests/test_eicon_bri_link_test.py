import hashlib
import socket
import unittest

from tools.eicon_bri_link_test import transfer


class TransferTest(unittest.TestCase):
    def test_binary_both_directions(self):
        a, b = socket.socketpair()
        with a, b:
            a.setblocking(False)
            b.setblocking(False)
            for src, dst, seed in ((a, b, b"forward"), (b, a, b"reverse")):
                result = transfer(src.fileno(), dst.fileno(), 65536, seed)
                self.assertTrue(result["ok"])
                self.assertEqual(result["sent"], 65536)
                self.assertEqual(result["received"], 65536)
                self.assertEqual(result["sha256"], hashlib.sha256(hashlib.shake_256(seed).digest(65536)).hexdigest())


if __name__ == "__main__":
    unittest.main()
