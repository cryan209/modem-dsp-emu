"""Bounded SVG rendering in a short-lived worker, without external fetches."""
import io
import subprocess
import sys
from pathlib import Path

MAX_SVG = 1024 * 1024

def svg_to_jpeg(source):
    if not source or len(source) > MAX_SVG:
        raise ValueError('SVG size limit')
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker'],
                                input=source, capture_output=True, timeout=6, check=True)
        if not result.stdout.startswith(b'\xff\xd8'):
            raise ValueError('SVG rendering failed')
        return result.stdout
    except subprocess.SubprocessError as error:
        raise ValueError('SVG rendering failed') from error

def worker(source):
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (384*1024*1024, 384*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU, (4, 4))
    from defusedxml import ElementTree
    from cairosvg.surface import PNGSurface
    from cairosvg.url import fetch
    from PIL import Image
    import re
    root = ElementTree.fromstring(source)
    if root.tag.rsplit('}',1)[-1] != 'svg':
        raise ValueError('Not SVG')
    def dimension(value, fallback):
        match = re.fullmatch(r'\s*([\d.]+)(?:px)?\s*',value or '')
        return float(match.group(1)) if match else fallback
    box = re.split(r'[\s,]+',root.get('viewBox','').strip())
    bw,bh = (float(box[2]),float(box[3])) if len(box)==4 else (300,150)
    width = dimension(root.get('width'),bw);height = dimension(root.get('height'),bh)
    if width<=0 or height<=0: raise ValueError('Invalid dimensions')
    scale = min(1,1024/width,768/height)
    width,height = max(1,round(width*scale)),max(1,round(height*scale))
    def safe_fetch(url, resource_type):
        if not url.startswith('data:') or len(url)>MAX_SVG:
            raise ValueError('External SVG resources are disabled')
        return fetch(url,resource_type)
    png = PNGSurface.convert(bytestring=source, output_width=width, output_height=height,
                             background_color='white', unsafe=False, url_fetcher=safe_fetch)
    with Image.open(io.BytesIO(png)) as image:
        out=io.BytesIO();image.convert('RGB').save(out,'JPEG',quality=65,optimize=True)
        return out.getvalue()

if __name__ == '__main__':
    data=sys.stdin.buffer.read(MAX_SVG+1)
    if len(data)>MAX_SVG: raise ValueError('SVG size limit')
    sys.stdout.buffer.write(worker(data))
