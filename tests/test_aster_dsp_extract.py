"""Parser checks for the Telindus IDMA boot format (addspv90guide.pdf 6.1)."""

import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from aster_dsp_extract import FormatError, parse  # noqa: E402

FIRMWARE = os.path.join(os.path.dirname(__file__), "..", "docs", "firmware",
                        "Aster 5 DSP", "T8660014.00")


def _load():
    with open(FIRMWARE, "rb") as fh:
        return fh.read()


@unittest.skipUnless(os.path.exists(FIRMWARE), "Aster 5 DSP image not present")
class AsterDspExtractTest(unittest.TestCase):
    def setUp(self):
        self.buf = _load()
        self.magic, self.images = parse(self.buf)

    def test_two_images_with_expected_identity(self):
        self.assertEqual(self.magic, 0x8002)
        self.assertEqual([i.header.config for i in self.images],
                         ["config:ASTDTP1", "config:ASTDTP2"])
        self.assertEqual([i.header.rcs for i in self.images],
                         ["rcs:JAN0904_11"] * 2)
        self.assertEqual(self.images[0].header.timestamp(), "2005-09-29 18:07")

    def test_checksum_is_the_byte_sum_of_the_body(self):
        for img in self.images:
            h = img.header
            body = self.buf[h.body_base:h.end]
            self.assertEqual(sum(body) & 0xFFFFFFFF, h.checksum)

    def test_images_tile_the_file_exactly(self):
        self.assertEqual(self.images[0].header.base, 0x000C)
        self.assertEqual(self.images[0].header.end, self.images[1].header.base)
        self.assertEqual(self.images[1].header.end, len(self.buf))

    def test_page_indices_are_the_guide_table_1_numbering(self):
        pages = {p.index: p for p in self.images[0].pages}
        self.assertEqual(sorted(pages), [0, 1, 2, 3, 6, 7, 8, 20])
        self.assertEqual(pages[8].name, "V.34")
        self.assertIsNotNone(self.images[0].startup)

    def test_v34_page_matches_the_eicon_overlay_size(self):
        # V34.ANA in dspdload.bin build 117-926 is DM 9328 / PM 10619.
        dm, pm = {p.index: p for p in self.images[0].pages}[8].word_counts()
        self.assertEqual((dm, pm), (9365, 11117))
        self.assertLess(abs(dm - 9328) / 9328, 0.01)

    def test_dial_page_initialises_the_six_words_at_0x3fa7(self):
        dial = {p.index: p for p in self.images[0].pages}[0]
        loaded = {a for b in dial.blocks if b.is_dm
                  for a in range(b.address, b.address + b.length)}
        self.assertTrue(set(range(0x3FA7, 0x3FAD)) <= loaded)
        self.assertNotIn(0x3FA6, loaded)
        self.assertNotIn(0x3FAD, loaded)

    def test_every_block_is_addressed_and_within_the_file(self):
        for img in self.images:
            self.assertTrue(img.header.has_overlay_field)
            self.assertFalse(img.header.compact_pm)
            for page in img.all_pages():
                self.assertTrue(page.blocks)
                for b in page.blocks:
                    self.assertEqual(b.overlay & 0x8000, 0x8000)
                    self.assertEqual(b.control & 0x8000, 0)
                    self.assertLessEqual(b.data_off + 2 * b.length, img.header.end)

    def test_rejects_a_truncated_body(self):
        short = bytearray(self.buf[:0x2000])
        with self.assertRaises(FormatError):
            parse(bytes(short))

    def test_rejects_an_implausible_header_offset(self):
        bad = bytearray(self.buf)
        struct.pack_into(">H", bad, 2, 0x0002)
        with self.assertRaises(FormatError):
            parse(bytes(bad))


if __name__ == "__main__":
    unittest.main()
