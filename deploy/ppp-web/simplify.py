"""Reduce static HTML to tags understood by a 1990s browser."""
from urllib.parse import urljoin, urlsplit, unquote_to_bytes
import base64
from bs4 import BeautifulSoup, Comment

ALLOWED = set('a p div br hr h1 h2 h3 h4 h5 h6 b strong i em u s strike pre code tt blockquote ul ol li dl dt dd table caption tr th td form input select option textarea img center sub sup address font'.split())
ATTRS = {
 'a': {'href','name'}, 'img': {'src','alt','width','height'},
 'form': {'action','method','enctype'},
 'input': {'type','name','value','checked','size','maxlength'},
 'select': {'name','multiple','size'}, 'option': {'value','selected'},
 'textarea': {'name','rows','cols'}, 'td': {'colspan','rowspan'},
 'th': {'colspan','rowspan'}, 'ol': {'start'},
}
BLOCKS = set('article main section header footer nav aside figure details dialog'.split())

def convert_inline(soup, svg_image):
    if svg_image is None:
        return
    for image in soup.find_all('img'):
        src=image.get('src','')
        if src.startswith('data:image/svg+xml') and len(src)<=1024*1024:
            try:
                prefix,data=src.split(',',1)
                raw=base64.b64decode(data,validate=True) if ';base64' in prefix else unquote_to_bytes(data)
                converted=svg_image(raw)
                if converted:image['src']=converted
            except (ValueError,TypeError):
                pass
    names = {x.lower():x for x in ('linearGradient','radialGradient','clipPath','textPath','foreignObject')}
    attrs = {x.lower():x for x in ('viewBox','preserveAspectRatio','gradientUnits','gradientTransform','patternUnits','patternTransform','markerWidth','markerHeight','refX','refY','textLength','lengthAdjust')}
    for svg in list(soup.find_all('svg'))[:16]:
        if svg.parent is None: continue
        alt = svg.get('aria-label') or (svg.title.get_text(' ',strip=True) if svg.title else 'SVG image')
        for node in [svg]+list(svg.find_all(True)):
            node.name=names.get(node.name,node.name)
            node.attrs={attrs.get(k,k):v for k,v in node.attrs.items()}
        svg['xmlns']='http://www.w3.org/2000/svg'
        src=svg_image(str(svg).encode('utf-8'))
        if src:
            image=soup.new_tag('img',src=src,alt=alt)
            svg.replace_with(image)


def simplify(source, url, plaintext_links=True, svg_image=None):
    soup = BeautifulSoup(source, 'html.parser')
    title = soup.title.get_text(' ', strip=True) if soup.title else 'Web page'
    base = urljoin(url, soup.base.get('href', '')) if soup.base else url
    convert_inline(soup, svg_image)
    body = soup.body or soup
    for node in list(body.find_all(string=lambda x: isinstance(x, Comment))):
        node.extract()
    for tag in list(body.find_all(['script','style','link','meta','base','head','template','iframe','object','embed'])):
        tag.decompose()
    for tag in list(body.find_all(True)):
        if tag.parent is None:
            continue
        if tag.has_attr('hidden') or tag.get('aria-hidden') == 'true':
            tag.decompose()
            continue
        if tag.name in BLOCKS:
            tag.name = 'div'
        elif tag.name == 'summary':
            tag.name = 'b'
        elif tag.name == 'figcaption':
            tag.name = 'p'
        elif tag.name == 'button':
            label = tag.get('value', tag.get_text(' ', strip=True))
            name = tag.get('name')
            tag.clear();tag.name = 'input'
            tag.attrs = {'type':'submit', 'value': label}
            if name: tag['name'] = name
        elif tag.name in ('svg','canvas','video','audio'):
            tag.replace_with(' ['+tag.get('aria-label', tag.name)+' omitted] ')
            continue
        if tag.name not in ALLOWED:
            tag.unwrap()
            continue
        if tag.name == 'img':
            if not tag.get('src') or tag.get('src','').startswith('data:'):
                candidate = tag.get('data-src') or tag.get('data-original')
                if not candidate and tag.get('srcset'):
                    candidate = tag['srcset'].split(',')[0].strip().split()[0]
                if candidate: tag['src'] = candidate
        tag.attrs = {key:value for key,value in tag.attrs.items() if key in ATTRS.get(tag.name,set())}
        for key in ('href','src','action'):
            if key not in tag.attrs: continue
            target = urljoin(base, tag[key])
            if urlsplit(target).scheme not in ('http','https','mailto','ftp'):
                del tag[key]
            else:
                tag[key] = target.replace('https://','http://',1) if plaintext_links and target.startswith('https://') else target
        if tag.name == 'input' and tag.get('type','text').lower() not in ('text','password','checkbox','radio','submit','reset','hidden','file','image'):
            tag['type'] = 'text'
    # Build a fresh document without site CSS, scripting, refreshes or charset conflicts.
    result = BeautifulSoup('<html><head></head><body bgcolor="#ffffff" text="#000000" link="#0000ee"></body></html>', 'html.parser')
    title_tag = result.new_tag('title');title_tag.string=title;result.head.append(title_tag)
    banner = result.new_tag('p')
    link = result.new_tag('a', href='http://192.168.1.100/')
    link.string='PPP Gateway';banner.append(link)
    banner.append(' | Simplified page | Switch modes at the gateway.')
    result.body.append(banner);result.body.append(result.new_tag('hr'))
    for child in list(body.contents): result.body.append(child.extract())
    text = result.body.get_text(' ',strip=True)
    if len(text) < 160:
        note = result.new_tag('p');note.string='If content is missing, this site may require JavaScript. This mode simplifies the HTML supplied by the website.'
        result.body.append(note)
    return str(result).encode('iso-8859-1', errors='xmlcharrefreplace')
