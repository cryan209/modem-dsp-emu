"""Transparent PPP web gateway for legacy browsers; no request/body logging."""
import io
import ipaddress
import re
import sys
import json
import hashlib
from collections import OrderedDict
from pathlib import Path
from urllib.parse import parse_qs
from mitmproxy import http, ctx
from PIL import Image, ImageOps
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stats
from simplify import simplify, convert_inline
from bs4 import BeautifulSoup
from rasterize import svg_to_jpeg
import reader
import software

import asyncio
import time

CLIENTS = {f'10.254.{n}.2' for n in range(91, 95)} | {'10.254.104.2', '10.254.104.6'}
LOCAL = ('192.168.1.100', '10.150.150.157')
LAN_NETWORKS = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
PORTAL_PORT = 8085
MAX_IMAGE_BYTES = 12 * 1024 * 1024
COMPAT_USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'
# mitmproxy opens upstream connections with no connect timeout, so a host that
# drops SYNs (xtra.co.nz, 202.27.184.102) leaves the caller's browser hanging
# for the kernel's ~2 minute SYN retry. Probe first and fail fast instead.
PROBE_TIMEOUT = 4.0
PROBE_OK_TTL = 600
PROBE_FAIL_TTL = 120
PROBE_CACHE_SIZE = 512
FAIL_FAST_ERROR = 'Upstream host not responding (fail-fast)'


def probe_exempt(host):
    """LAN and gateway destinations are local and never probed."""
    if host in LOCAL:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host.endswith(('.local', '.lan', '.nz.invalid')) or '.' not in host
    return any(address in net for net in LAN_NETWORKS) or address.is_loopback


class UpstreamProbe:
    """TCP reachability check with a bounded positive/negative cache."""

    def __init__(self, timeout=PROBE_TIMEOUT, ok_ttl=PROBE_OK_TTL, fail_ttl=PROBE_FAIL_TTL,
                 size=PROBE_CACHE_SIZE, clock=time.monotonic):
        self.timeout, self.ok_ttl, self.fail_ttl, self.size, self.clock = timeout, ok_ttl, fail_ttl, size, clock
        self.cache = OrderedDict()

    async def reachable(self, host, port):
        key = (host, port)
        entry = self.cache.get(key)
        now = self.clock()
        if entry and entry[1] > now:
            self.cache.move_to_end(key)
            return entry[0]
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), self.timeout)
        except (OSError, asyncio.TimeoutError):
            ok = False
        else:
            ok = True
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
        self.cache[key] = (ok, now + (self.ok_ttl if ok else self.fail_ttl))
        self.cache.move_to_end(key)
        while len(self.cache) > self.size:
            self.cache.popitem(last=False)
        return ok

class LegacyWeb:
    def __init__(self):
        self.modes = {}
        self.svg_cache = OrderedDict()
        self.probe = UpstreamProbe()

    async def server_connect(self, data):
        address = data.server.address
        if not address or probe_exempt(address[0]):
            return
        if not await self.probe.reachable(address[0], address[1]):
            data.server.error = FAIL_FAST_ERROR

    def svg_image(self, source):
        key = hashlib.sha256(source).hexdigest()
        if key not in self.svg_cache:
            try:
                data = svg_to_jpeg(source)
            except ValueError:
                return None
            self.svg_cache[key] = data
            stats.web['images'] += 1
            stats.web['original'] += len(source)
            stats.web['compressed'] += len(data)
            while len(self.svg_cache)>128 or sum(map(len,self.svg_cache.values()))>8*1024*1024:
                self.svg_cache.popitem(last=False)
        return 'http://192.168.1.100/svg/'+key+'.jpg'

    def load(self, loader):
        try:
            self.modes = json.loads((Path(ctx.options.confdir)/'page-modes.json').read_text())
        except (OSError, ValueError):
            self.modes = {}

    def client_connected(self, client):
        address = ipaddress.ip_address(client.peername[0])
        portal = client.sockname and client.sockname[1] == PORTAL_PORT
        if client.peername[0] not in CLIENTS and not (portal and any(address in net for net in LAN_NETWORKS)):
            client.error = 'This gateway is for PPP callers and LAN/VPN portal users only.'

    def request(self, flow):
        req = flow.request
        portal = flow.client_conn.sockname and flow.client_conn.sockname[1] == PORTAL_PORT
        flow.metadata['lan_portal'] = bool(portal)
        if portal and req.pretty_host not in LOCAL:
            flow.response = http.Response.make(403, b'Local portal only')
            return
        # Transparent mode may initially identify the destination by its IP.
        # Use the HTTP Host name for upstream routing and TLS SNI.
        host = req.pretty_host
        req.host = host
        flow.metadata['legacy_http'] = req.scheme == 'http'
        client = flow.client_conn.peername[0]
        flow.metadata['simplify'] = self.modes.get(client) == 'simple'
        flow.metadata['reader'] = self.modes.get(client) == 'reader'
        if req.host in LOCAL:
            if req.path.split('?')[0] == '/account' or req.path.startswith('/account/'):
                flow.response = http.Response.make(302, b'', {'Location': 'http://10.150.150.157:8087'+req.path, 'Cache-Control': 'no-store'})
                return
            if req.path.split('?')[0] in ('/status', '/irc', '/homepages', '/signup', '/register') or req.path.startswith('/~'):
                flow.response = http.Response.make(302, b'', {'Location': 'http://10.150.150.157:8086'+req.path, 'Cache-Control': 'no-store'})
                return
            if req.path.split('?')[0] == '/upload':
                flow.response = http.Response.make(302,b'',{'Location':'http://192.168.1.100:83/upload','Cache-Control':'no-store'})
                return
            if req.path.split('?')[0] in ('/software', '/drivers', '/updates'):
                flow.response = http.Response.make(200, software.page(), {'Content-Type':'text/html; charset=iso-8859-1','Cache-Control':'no-store'})
                return
            if req.path.startswith('/svg/'):
                key = req.path.split('/')[-1].split('.')[0]
                data = self.svg_cache.get(key)
                flow.response = http.Response.make(200 if data else 404, data or b'Image expired; reload the page.',
                    {'Content-Type':'image/jpeg' if data else 'text/plain', 'Cache-Control':'private, max-age=300'})
                flow.metadata['svg_cached'] = True
                return
            if req.path.split('?')[0] == '/settings' and req.method == 'POST':
                mode = parse_qs(req.get_text(strict=False)).get('mode', ['normal'])[0]
                if mode in ('normal', 'simple', 'reader'):
                    self.modes[client] = mode
                    path = Path(ctx.options.confdir)/'page-modes.json'
                    temporary = path.with_suffix('.tmp')
                    temporary.write_text(json.dumps(self.modes))
                    temporary.replace(path)
                flow.response = http.Response.make(303, b'', {'Location':'http://192.168.1.100/', 'Cache-Control':'no-store'})
                return
            if req.path.split('?')[0] in ('/stats', '/stats.htm', '/stats.json'):
                data = stats.snapshot(flow.client_conn.peername[0])
                is_json = req.path.split('?')[0] == '/stats.json'
                body = json.dumps(data) if is_json else stats.page(data)
                flow.response = http.Response.make(200, body.encode('ascii'), {
                    'Content-Type': 'application/json' if is_json else 'text/html; charset=iso-8859-1',
                    'Cache-Control': 'no-store'})
                return
            if req.path.split('?')[0] in ('/ca.cer', '/ca.pem'):
                cert = Path(ctx.options.confdir).expanduser() / 'mitmproxy-ca-cert.pem'
                if cert.exists():
                    flow.response = http.Response.make(200, cert.read_bytes(), {
                        'Content-Type': 'application/x-x509-ca-cert',
                        'Cache-Control': 'no-store'})
                    return
            mode = {'simple':'Simplified','reader':'Reader'}.get(self.modes.get(client),'Normal')
            page = '''<HTML><HEAD><TITLE>PPP Web Gateway</TITLE></HEAD>
<BODY><H1>PPP Web Gateway</H1><P><A HREF="http://10.150.150.157:8087/account"><B>My ISP account</B></A></P><P><A HREF="http://10.150.150.157:8086/signup"><B>Join Xtra ISP</B></A> - one login for PPP, mail and personal web space.</P><P><A HREF="http://10.150.150.157:8086/status">ISP status</A> | <A HREF="http://10.150.150.157:8086/irc">IRC chat</A> | <A HREF="http://10.150.150.157:8086/homepages">Personal homepages</A></P><P>Automatic web proxy and image compression are active.</P>
<P>Page mode: <B>'''+mode+'''</B></P>
<FORM METHOD="POST" ACTION="http://192.168.1.100/settings">
<INPUT TYPE="HIDDEN" NAME="mode" VALUE="simple"><INPUT TYPE="SUBMIT" VALUE="Simplify pages"></FORM>
<FORM METHOD="POST" ACTION="http://192.168.1.100/settings">
<INPUT TYPE="HIDDEN" NAME="mode" VALUE="reader"><INPUT TYPE="SUBMIT" VALUE="Reader mode"></FORM>
<FORM METHOD="POST" ACTION="http://192.168.1.100/settings">
<INPUT TYPE="HIDDEN" NAME="mode" VALUE="normal"><INPUT TYPE="SUBMIT" VALUE="Normal pages"></FORM>
<P>Reader mode extracts articles and story links. Public JavaScript pages may take up to 35 seconds to load on the server.</P>
<P><A HREF="http://www.stuff.co.nz/">Stuff news</A></P>
<P>Simplified mode keeps text, links, forms and images, and removes scripts and modern layout.
It cannot run sites that require JavaScript. Your choice applies to this dial-in client address.</P>
<P>For old Netscape, enter sites as http://example.com/ -- the gateway connects
using HTTPS on your behalf. No proxy settings are needed.</P>
<P><A HREF="/stats">Connection stats</A> | <A HREF="/software">Drivers and Updates</A> | <A HREF="/upload">Upload PCI report</A></P>
<P><A HREF="/ca.cer">Install the gateway certificate</A> for HTTPS-capable browsers.</P>
<P><A HREF="http://example.com/">Test a website</A></P></BODY></HTML>'''
            flow.response = http.Response.make(200, page.encode('ascii'),
                {'Content-Type': 'text/html; charset=iso-8859-1', 'Cache-Control':'no-store'})
            return
        stats.web['requests'] += 1
        # Upgrade ordinary HTTP website requests to verified modern HTTPS.
        # Keep private/LAN HTTP services and nonstandard ports unchanged.
        try:
            public = ipaddress.ip_address(req.host).is_global
        except ValueError:
            public = '.' in req.host and not req.host.endswith(('.local', '.lan', '.nz.invalid'))
        # Transparent routing already supplies the destination IP. An internal
        # service can have a dotted DNS name while supporting HTTP only.
        destination = flow.server_conn.address
        if destination:
            try:
                public = public and ipaddress.ip_address(destination[0]).is_global
            except ValueError:
                pass
        # Old browser signatures are rejected by some otherwise readable sites.
        # Change only legacy browser identification on public web requests.
        if public and re.match(r'^Mozilla/[1-4]\.', req.headers.get('user-agent', ''), re.I):
            req.headers['user-agent'] = COMPAT_USER_AGENT
        if req.scheme == 'http' and req.port == 80 and public:
            req.scheme = 'https'
            req.port = 443
            flow.server_conn.address = (req.host, 443)
            flow.server_conn.sni = req.host
        # Avoid modern image formats and compressed HTML on the PPP side.
        req.headers['accept-encoding'] = 'identity'
        req.headers['accept'] = req.headers.get('accept', '*/*').replace('image/avif', 'image/jpeg').replace('image/webp', 'image/jpeg')

    def error(self, flow):
        stats.web['errors'] += 1

    async def response(self, flow):
        res = flow.response
        if res is None or res.raw_content is None or flow.metadata.get('svg_cached'):
            return
        if flow.metadata.get('lan_portal'):
            def portal_links(value):
                return value.replace('http://192.168.1.100:83', 'http://10.150.150.157:8083').replace('http://192.168.1.100', 'http://10.150.150.157:8085')
            for header in ('location', 'refresh'):
                if header in res.headers:
                    res.headers[header] = portal_links(res.headers[header])
            if 'text/html' in res.headers.get('content-type', '').lower():
                res.content = portal_links(res.get_text(strict=False)).encode('ascii', errors='xmlcharrefreplace')
            return
        legacy = flow.metadata.get('legacy_http', False)
        kind = res.headers.get('content-type', '').lower().split(';')[0]
        if legacy:
            for header in ('strict-transport-security', 'content-security-policy',
                           'content-security-policy-report-only', 'alt-svc'):
                res.headers.pop(header, None)
            for header in ('location', 'refresh'):
                if header in res.headers:
                    res.headers[header] = res.headers[header].replace('https://', 'http://')
            cookies = res.headers.get_all('set-cookie')
            if cookies:
                res.headers.set_all('set-cookie', [re.sub(r';\s*secure\b', '', c, flags=re.I) for c in cookies])
        if kind in ('text/html','application/xhtml+xml') and flow.request.host not in LOCAL and reader.is_challenge(res.get_text(strict=False),res.headers):
            res.content=reader.challenge_page(flow.request.pretty_url)
            res.headers['content-type']='text/html; charset=iso-8859-1'
            res.headers['cache-control']='no-store'
            res.headers['x-ppp-challenge']='detected'
            res.headers.pop('refresh',None)
            res.headers.pop('transfer-encoding',None)
            res.headers['content-length']=str(len(res.raw_content))
            res.headers['connection']='close'
            res.http_version='HTTP/1.0'
            return
        changed = False
        if flow.metadata.get('reader') and flow.request.host not in LOCAL and kind in ('text/html','application/xhtml+xml'):
            source = res.get_text(strict=False)
            if flow.request.method == 'GET' and res.status_code == 200:
                if reader.needs_render(source,flow.request.pretty_url):
                    rendered = await reader.rendered(flow.request.pretty_url)
                    if rendered:
                        if reader.is_challenge(rendered):
                            res.content=reader.challenge_page(flow.request.pretty_url)
                            res.headers['content-type']='text/html; charset=iso-8859-1'
                            res.headers['cache-control']='no-store'
                            res.headers['x-ppp-challenge']='detected'
                            res.headers.pop('refresh',None)
                            res.headers.pop('transfer-encoding',None)
                            res.headers['content-length']=str(len(res.raw_content))
                            res.headers['connection']='close'
                            res.http_version='HTTP/1.0'
                            return
                        source = rendered
            try:
                res.content = reader.extract(source, flow.request.pretty_url, legacy, self.svg_image)
                res.headers['content-type'] = 'text/html; charset=iso-8859-1'
                res.headers['cache-control'] = 'no-store'
                res.headers['x-ppp-page-mode'] = 'reader'
                changed = True
            except (ValueError, UnicodeError):
                pass
        if flow.metadata.get('simplify') and flow.request.host not in LOCAL and kind in ('text/html','application/xhtml+xml'):
            try:
                res.content = simplify(res.get_text(strict=False), flow.request.pretty_url, legacy, self.svg_image)
                res.headers['content-type'] = 'text/html; charset=iso-8859-1'
                res.headers['cache-control'] = 'no-store'
                res.headers['x-ppp-page-mode'] = 'simplified'
                changed = True
            except (ValueError, UnicodeError):
                pass
        if not flow.metadata.get('simplify') and not flow.metadata.get('reader') and flow.request.host not in LOCAL and kind in ('text/html','application/xhtml+xml'):
            text = res.get_text(strict=False)
            if '<svg' in text.lower():
                soup = BeautifulSoup(text, 'html.parser')
                convert_inline(soup, self.svg_image)
                res.text = str(soup)
                changed = True
        if kind == 'image/svg+xml':
            try:
                original = res.content
                data = svg_to_jpeg(original)
                res.content = data
                res.headers['content-type'] = 'image/jpeg'
                res.headers['x-ppp-image-bytes'] = f'{len(original)} -> {len(data)}'
                stats.web['images'] += 1
                stats.web['original'] += len(original)
                stats.web['compressed'] += len(data)
                changed = True
            except ValueError:
                pass
        if legacy and kind in ('text/html', 'text/css', 'application/xhtml+xml'):
            try:
                text = res.get_text(strict=False)
                text = text.replace('https://', 'http://').replace(r'https:\/\/', r'http:\/\/')
                res.text = text
                changed = True
            except (ValueError, UnicodeError):
                pass
        if kind.startswith('image/') and kind != 'image/svg+xml' and len(res.raw_content) <= MAX_IMAGE_BYTES:
            try:
                original = res.content
                with Image.open(io.BytesIO(original)) as image:
                    if getattr(image, 'is_animated', False):
                        raise ValueError('Preserve animations')
                    image = ImageOps.exif_transpose(image)
                    image.thumbnail((1024, 768))
                    output = io.BytesIO()
                    alpha = image.mode in ('RGBA', 'LA') or 'transparency' in image.info
                    if alpha:
                        image.convert('RGBA').quantize(colors=128).save(output, format='GIF')
                        mime = 'image/gif'
                    else:
                        image.convert('RGB').save(output, format='JPEG', quality=55, optimize=True)
                        mime = 'image/jpeg'
                    data = output.getvalue()
                    if len(data) < len(original) or kind in ('image/webp', 'image/avif', 'image/png'):
                        stats.web['images'] += 1
                        stats.web['original'] += len(original)
                        stats.web['compressed'] += len(data)
                        res.content = data
                        res.headers['content-type'] = mime
                        res.headers['x-ppp-image-bytes'] = f'{len(original)} -> {len(data)}'
                        changed = True
            except (OSError, ValueError, Image.DecompressionBombError):
                pass
        if changed:
            for header in ('etag', 'content-md5', 'digest', 'accept-ranges'):
                res.headers.pop(header, None)
        # Old browsers often cannot decode modern HTTP content encodings.
        if legacy and res.headers.get('content-encoding'):
            try:
                res.decode()
            except ValueError:
                pass
        if legacy or flow.request.http_version == 'HTTP/1.0':
            res.http_version = 'HTTP/1.0'
            res.headers.pop('transfer-encoding', None)
            res.headers['connection'] = 'close'
            res.headers['content-length'] = str(len(res.raw_content))

addons = [LegacyWeb()]
