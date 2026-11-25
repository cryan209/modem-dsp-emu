import importlib.util
from pathlib import Path
import tempfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'deploy/isp-registration'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'deploy/retro-isp'))
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('retro_web', Path(__file__).resolve().parents[1]/'deploy/retro-isp/web.py')
web = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(web)


class PersonalWebTests(unittest.TestCase):
    def test_personal_root_and_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'users'; user=root/'snuggi'; user.mkdir(parents=True)
            index=user/'index.html'; index.write_text('homepage')
            outside=Path(directory)/'private.txt'; outside.write_text('private')
            (user/'leak.txt').symlink_to(outside)
            with patch.object(web,'USERS',root):
                self.assertEqual(web.personal_file('/~snuggi/'),index)
                for path in ('/~snuggi/../private.txt','/~snuggi/%2e%2e/private.txt','/~snuggi/leak.txt','/~snuggi/.hidden','/~snuggi/%00','/~snuggi/..\\private.txt'):
                    self.assertIsNone(web.personal_file(path),path)
                (user/'index.html').unlink(); (user/'index.html').symlink_to(outside)
                self.assertIsNone(web.personal_file('/~snuggi/'))

    def test_page_escapes_dynamic_text(self):
        page=web.page('<test>', '<P>Content</P>')
        self.assertIn(b'&lt;test&gt;',page)
        self.assertNotIn(b'<script',page)


if __name__=='__main__':unittest.main()
