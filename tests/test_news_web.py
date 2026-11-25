import sys
from pathlib import Path
import unittest
from email.message import EmailMessage
from unittest.mock import Mock, patch
from io import BytesIO
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'deploy/retro-isp'))
import news_page as n


class NewsWebTests(unittest.TestCase):
    def row(self, number, mid, refs=(), subject='Subject'):
        return dict(number=number, mid=mid, refs=list(refs), subject=subject, author='<script>bad</script>', date='Today')

    def test_threads_escape_and_preserve_replies_even_with_cycle(self):
        rows = [self.row(1,'<a>',subject='<img src=x onerror=bad>'), self.row(2,'<b>', ['<a>']),
                self.row(3,'<c>',['<d>']), self.row(4,'<d>',['<c>'])]
        html = n.render_threads('xtra.help',rows)
        self.assertNotIn('<script>',html)
        self.assertNotIn('<img ',html)
        self.assertIn('&lt;script&gt;',html)
        for number in (1,2,3,4):
            self.assertEqual(html.count('article='+str(number)+'"'),1)
        self.assertIn('<UL><LI>',html)

    def test_invalid_paths_never_connect(self):
        with patch.object(n, 'connection') as connect:
            for values in ({'group':['control']},{'group':['xtra.help\r\nQUIT']},
                           {'group':['xtra.help'],'article':['1\r\nLIST']},
                           {'group':['xtra.help'],'article':['-1']}, {'article':['1']}):
                with self.assertRaises(n.NewsError):
                    n.render(values)
            connect.assert_not_called()

    def test_mime_plain_text_and_no_html_execution(self):
        message = EmailMessage()
        message.set_content('plain <script>text</script>')
        message.add_alternative('<script>evil</script>',subtype='html')
        self.assertIn('plain', n.plain_body(message))
        self.assertNotIn('evil', n.plain_body(message))
        message = EmailMessage()
        message.set_content('<script>evil</script>',subtype='html')
        self.assertIn('No plain-text body', n.plain_body(message))

    def test_nntp_dot_unstuff_and_size_bound(self):
        client=n.NNTP(BytesIO(b'..leading dot\r\n.\r\n'))
        self.assertEqual(client.multiline(),[b'.leading dot'])
        with self.assertRaises(n.NewsError):
            n.NNTP(BytesIO(b'large\r\n.\r\n')).multiline(maximum=3)

    def test_article_links_resolve_inside_selected_group_and_escape_body(self):
        raw=(b'From: Fiction <demo@retro.invalid>\r\nSubject: Test\r\n'
             b'Message-ID: <b>\r\nReferences: <a>\r\n\r\n<script>alert(1)</script>')
        client=Mock()
        client.multiline.return_value=raw.split(b'\r\n')
        client.overview.return_value=[self.row(1,'<a>'),self.row(2,'<b>',['<a>']),self.row(3,'<c>',['<a>','<b>'])]
        html=n.render_article(client,'xtra.help',2,1,3)
        self.assertIn('In reply to:',html)
        self.assertIn('Replies',html)
        self.assertIn('article=1',html)
        self.assertIn('article=3',html)
        self.assertNotIn('<script>',html)
        self.assertIn('&lt;script&gt;',html)

    def test_large_deep_thread_does_not_recurse_or_drop_articles(self):
        rows=[self.row(i+1,'<%s>'%i, ['<%s>'%(i-1)] if i else []) for i in range(1000)]
        html=n.render_threads('xtra.help',rows)
        self.assertEqual(html.count('<LI>'),1000)
        self.assertIn('999 replies in this range',html)
        self.assertIn('article=1000',html)

    def test_page_size_bounds_and_navigation(self):
        with patch.object(n,'connection') as connection:
            client=connection.return_value.__enter__.return_value
            client.group.return_value=(1300,1,1300)
            client.overview.return_value=[]
            html=n.render({'group':['xtra.help'],'limit':['1000'],'before':['1101']})
            client.overview.assert_called_once_with(101,1100)
            self.assertIn('before=101&amp;limit=1000',html)
            self.assertIn('1000 article numbers',html)
        with patch.object(n,'connection') as connection:
            with self.assertRaises(n.NewsError):
                n.render({'group':['xtra.help'],'limit':['1001']})
            connection.assert_not_called()

    def test_historical_reader_uses_local_corpus_and_preserves_old_dates(self):
        import tempfile, json, base64, hashlib
        import history_archive as archive
        message=EmailMessage()
        message['From']='Historic Author <unavailable@archive.retro.invalid>'
        message['Subject']='An actual old discussion'
        message['Date']='Thu, 25 Jan 1996 12:00:00 +0000'
        message['Message-ID']='<historical@example.invalid>'
        message['Newsgroups']='archive.nz.general'
        message['X-Archive-Source']='https://groups.google.com/g/nz.general/c/example'
        message.set_content('Old text <script>not executable</script>\n.dot remains intact')
        raw=message.as_bytes()
        record=dict(group='archive.nz.general',mid=str(message['Message-ID']),article_b64=base64.b64encode(raw).decode(),sha256=hashlib.sha256(raw).hexdigest())
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'history.json';path.write_text(json.dumps([record]))
            archive.load.cache_clear()
            try:
                with patch.object(archive,'PATH',path),patch.object(n,'connection') as network:
                    html=n.render({'group':['archive.nz.general'],'article':['1']})
                    network.assert_not_called()
                    self.assertIn('1996',html)
                    self.assertIn('Original archive source',html)
                    self.assertIn('&lt;script&gt;',html)
                    self.assertIn('.dot remains intact',html)
                    self.assertNotIn('<script>',html)
                    reader=archive.Reader();self.assertEqual(reader.group('archive.nz.general'),(1,1,1))
                    with self.assertRaises(ValueError):reader.command('POST',(b'340',))
            finally:archive.load.cache_clear()

    def test_archive_expansion_preserves_existing_numbers_when_older_posts_arrive(self):
        import tempfile,json,base64,hashlib
        import history_archive as archive
        records=[]
        for number,year in ((1,1997),(2,1995)):
            m=EmailMessage();m['From']='Archive <unavailable@archive.retro.invalid>'
            m['Date']='01 Jan %s 12:00:00 +0000'%year
            m['Message-ID']='<stable-%s@example.invalid>'%number
            m['Subject']='Post %s'%number;m.set_content('Body')
            raw=m.as_bytes()
            records.append(dict(group='archive.nz.general',mid=str(m['Message-ID']),web_number=number,
                                article_b64=base64.b64encode(raw).decode(),sha256=hashlib.sha256(raw).hexdigest()))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'records.json';path.write_text(json.dumps(records))
            archive.load.cache_clear()
            try:
                with patch.object(archive,'PATH',path):
                    reader=archive.Reader();reader.group('archive.nz.general')
                    self.assertEqual([r['mid'] for r in reader.overview(1,2)],['<stable-1@example.invalid>','<stable-2@example.invalid>'])
            finally:archive.load.cache_clear()
