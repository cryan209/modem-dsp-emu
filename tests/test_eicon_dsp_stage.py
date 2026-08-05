"""Staging a download the card's own file set omits, and resolving which one.

The PRI file set (5) does not carry the V.90 APCM overlay, and `te_dmlt.pm`
decides V.90A is unsupported by *searching the staged table* for id 0x026b
and not finding it (0x80091f9c, tracing "V.90A not supported" at 0x80092014).
The staged table is this harness's to build, so the capability is reachable by
staging the overlay -- which is what `extra_download_ids` is for.

An id alone does not name a record: the combifile ships 0x026b twice, once as
"V.90 APCM Overlay" and once as "V90.ANA APCM Overlay". These pin the rule
that picks between them -- the variant belonging to a file set that runs the
same 0x0258 task kernel -- because getting that wrong stages analogue-card
code onto an F34 task and the failure would be inside the DSP, a long way from
here.

These need `docs/firmware/dspdload.bin`, which is tracked.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import eicon_dsp_stage as stage
from eicon_dsp_extract import FormatError, parse_combifile

COMBIFILE = ROOT / 'docs' / 'firmware' / 'dspdload.bin'
PRI_BASE = 0x80338700  # te_dmlt.pm's OFFS_PROTOCOL_END_ADDR

CARDTYPE_DIVASRV_Q_8M_V2_PCI = 53   # file set 9, the 4BRI that ships APCM
CARDTYPE_ANALOG = 77                # file set 18, the .ANA task family


class FileSetTests(unittest.TestCase):
    """What each file set carries, which is the premise of everything below."""

    @classmethod
    def setUpClass(cls):
        cls.combi = parse_combifile(COMBIFILE)

    def selected_ids(self, card_type):
        selected, file_set = stage.required_downloads(self.combi, card_type)
        return {download['download_id'] for download in selected}, file_set

    def test_the_pri_file_set_has_dpcm_and_not_apcm(self):
        ids, file_set = self.selected_ids(stage.CARDTYPE_DIVASRV_P_30M_PCI)
        self.assertEqual(file_set, 5)
        self.assertIn(0x026A, ids)
        self.assertNotIn(stage.DOWNLOAD_V90_APCM, ids)

    def test_the_4bri_v2_file_set_has_both(self):
        ids, file_set = self.selected_ids(CARDTYPE_DIVASRV_Q_8M_V2_PCI)
        self.assertEqual(file_set, 9)
        self.assertIn(0x026A, ids)
        self.assertIn(stage.DOWNLOAD_V90_APCM, ids)

    def test_the_pri_and_the_4bri_v2_share_a_task_kernel(self):
        """Which is what makes the 4BRI's APCM overlay stageable on the PRI."""
        family = stage._compatible_file_sets(self.combi, 5)
        self.assertIn(9, family)
        self.assertNotIn(18, family)


class ResolveExtraDownloadTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.combi = parse_combifile(COMBIFILE)
        cls.pri_selected, cls.pri_file_set = stage.required_downloads(
            cls.combi, stage.CARDTYPE_DIVASRV_P_30M_PCI)

    def test_apcm_resolves_to_the_non_ana_variant_for_the_pri(self):
        download = stage.resolve_extra_download(
            self.combi, stage.DOWNLOAD_V90_APCM, self.pri_file_set,
            self.pri_selected)
        self.assertEqual(download['download_id'], stage.DOWNLOAD_V90_APCM)
        self.assertIn('V.90 APCM', download['description'])
        self.assertNotIn('ANA', download['description'])

    def test_a_download_the_file_set_already_has_is_refused(self):
        with self.assertRaises(FormatError) as caught:
            stage.resolve_extra_download(self.combi, 0x026A,
                                         self.pri_file_set, self.pri_selected)
        self.assertIn('already in file set 5', str(caught.exception))

    def test_no_variant_in_the_task_family_is_refused(self):
        """0x026a exists, but not for the .ANA family -- an error, not a swap."""
        selected, file_set = stage.required_downloads(
            self.combi, CARDTYPE_ANALOG)
        self.assertEqual(file_set, 18)
        with self.assertRaises(FormatError) as caught:
            stage.resolve_extra_download(self.combi, 0x026A, file_set, selected)
        self.assertIn('task kernel', str(caught.exception))


class StagedImageTests(unittest.TestCase):

    def build(self, **kwargs):
        return stage.build_dsp_code_image(
            COMBIFILE, stage.CARDTYPE_DIVASRV_P_30M_PCI, PRI_BASE, **kwargs)

    def test_staging_apcm_adds_exactly_one_download(self):
        plain = self.build()
        with_apcm = self.build(
            extra_download_ids=(stage.DOWNLOAD_V90_APCM,))
        self.assertEqual(len(with_apcm.downloads), len(plain.downloads) + 1)
        self.assertEqual(with_apcm.file_set, plain.file_set)

    def test_the_extra_download_is_appended_and_addressable(self):
        image = self.build(extra_download_ids=(stage.DOWNLOAD_V90_APCM,))
        last = image.downloads[-1]
        self.assertEqual(last.download_id, stage.DOWNLOAD_V90_APCM)
        self.assertGreater(last.size, 0)
        self.assertGreaterEqual(last.address, image.base_addr)
        self.assertLessEqual(last.address + last.size, image.end_addr)

    def test_the_existing_downloads_are_unmoved(self):
        """Appending must not relocate what the known-good runs already load."""
        plain = self.build()
        with_apcm = self.build(
            extra_download_ids=(stage.DOWNLOAD_V90_APCM,))
        self.assertEqual(
            [(d.download_id, d.address, d.size) for d in plain.downloads],
            [(d.download_id, d.address, d.size)
             for d in with_apcm.downloads[:len(plain.downloads)]])

    def test_the_count_word_matches_the_table(self):
        import struct
        image = self.build(extra_download_ids=(stage.DOWNLOAD_V90_APCM,))
        count = struct.unpack_from('<I', image.data, 0)[0]
        self.assertEqual(count, len(image.downloads))

    def test_the_table_still_has_room(self):
        image = self.build(extra_download_ids=(stage.DOWNLOAD_V90_APCM,))
        self.assertLessEqual(len(image.downloads),
                             stage.DSP_MAX_DOWNLOAD_COUNT)

    def test_no_extras_reproduces_the_default_image_byte_for_byte(self):
        self.assertEqual(self.build(extra_download_ids=()).data,
                         self.build().data)


if __name__ == '__main__':
    unittest.main()
