"""Reader extraction with optional server-side Chromium for public pages."""
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
from html import escape
from bs4 import BeautifulSoup
from readability import Document
from simplify import simplify

LIMIT = asyncio.Semaphore(2)

def is_challenge(source, headers=None):
    if headers and headers.get('cf-mitigated', '').lower() == 'challenge':
        return True
    soup=BeautifulSoup(source,'html.parser')
    title=soup.title.get_text(' ',strip=True).lower() if soup.title else ''
    return title in ('just a moment...', 'just a moment', 'verify you are human',
                     'security verification', 'attention required! | cloudflare')

def needs_render(source, url):
    soup=BeautifulSoup(source,'html.parser')
    for node in soup(['script','style','head','nav','footer']):node.decompose()
    text=sum(len(p.get_text(' ',strip=True)) for p in soup.find_all('p'))
    if text>=500:return False
    links=[a for a in soup.find_all('a',href=True) if len(a.get_text(' ',strip=True))>=25]
    if len(links)>=5:return False
    # Sparse JavaScript shells benefit from rendering; short static pages do not.
    return '<script' in source.lower() and text<500

def challenge_page(url):
    return ('''<HTML><HEAD><TITLE>Site requires browser verification</TITLE></HEAD>
<BODY><H1>Site requires browser verification</H1>
<P>This website returned a bot challenge. The gateway has stopped here; it will
not automatically retry or attempt to solve the challenge.</P>
<P>The Win95 browser cannot reliably complete modern verification pages.
Open this site in a current browser, or try the publisher's public RSS feed
or text edition if one is available.</P><P>Site: '''+escape(url)+'''</P>
<P><A HREF="http://192.168.1.100/">Gateway settings</A></P></BODY></HTML>''').encode('ascii',errors='xmlcharrefreplace')

def extract(source, url, plaintext, svg_image=None):
    soup = BeautifulSoup(source,'html.parser')
    title = soup.title.get_text(' ',strip=True) if soup.title else 'Article'
    author = soup.find('meta',attrs={'name':'author'}) or soup.find('meta',attrs={'property':'article:author'})
    date = soup.find('meta',attrs={'property':'article:published_time'})
    path=urlsplit(url).path.strip('/')
    if not path or path in ('nz','world','sport','business','life-style','travel','culture'):
        # Home/section pages need a story list rather than a single extracted article.
        content = BeautifulSoup('<div><h1></h1><ul></ul></div>','html.parser')
        content.h1.string=title
        seen=set()
        for a in soup.find_all('a',href=True):
            label=a.get_text(' ',strip=True)
            if len(label)<25 or a['href'] in seen:continue
            seen.add(a['href'])
            li=content.new_tag('li');link=content.new_tag('a',href=a['href']);link.string=label[:300];li.append(link);content.ul.append(li)
            if len(seen)>=100:break
        article=str(content)
    else:
        doc=Document(source,url=url)
        article=doc.summary(html_partial=True)
        title=doc.short_title() or title
        content=BeautifulSoup('<div><h1></h1><p></p><div id="article"></div></div>','html.parser')
        content.h1.string=title
        byline=[]
        if author and author.get('content'):byline.append(author['content'])
        if date and date.get('content'):byline.append(date['content'])
        content.p.string=' | '.join(byline)
        content.find(id='article').append(BeautifulSoup(article,'html.parser'))
        article=str(content)
    result=simplify(article,url,plaintext,svg_image).decode('iso-8859-1')
    return result.replace('Simplified page','Reader mode').encode('iso-8859-1')

async def rendered(url):
    async with LIMIT:
        process=await asyncio.create_subprocess_exec(sys.executable,str(Path(__file__).resolve()),'--render',url,
                   stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True)
        try:
            out,err=await asyncio.wait_for(process.communicate(),timeout=35)
            if process.returncode or len(out)>6*1024*1024:
                return None
            return out.decode('utf-8')
        except (asyncio.TimeoutError, asyncio.CancelledError):
            import os,signal
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            await process.wait()
            return None

async def render_worker(url):
    from playwright.async_api import async_playwright
    import ipaddress,socket,os,tempfile
    scratch=tempfile.TemporaryDirectory(prefix="ppp-reader-")
    browser_env={**os.environ,"HOME":scratch.name,"XDG_CONFIG_HOME":scratch.name,"XDG_CACHE_HOME":scratch.name}
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True, env=browser_env,
                   args=['--disable-dev-shm-usage','--disable-background-networking'])
        context=await browser.new_context(viewport={'width':1280,'height':900},service_workers='block')
        async def route(request_route):
            req=request_route.request
            parsed=urlsplit(req.url)
            if parsed.scheme not in ('http','https') or req.resource_type in ('image','media','font'):
                await request_route.abort();return
            try:
                addresses=await asyncio.to_thread(socket.getaddrinfo,parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80))
                if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                    await request_route.abort();return
            except (OSError,ValueError):
                await request_route.abort();return
            await request_route.continue_()
        await context.route('**/*',route)
        page=await context.new_page()
        try:
            await page.goto(url,wait_until='domcontentloaded',timeout=22000)
            initial=await page.content()
            if is_challenge(initial):
                sys.stdout.write(initial)
                return
            try:await page.wait_for_selector('article p, main p, h1',timeout=5000)
            except Exception:pass
            await page.wait_for_timeout(1500)
            # Exclude hidden content from extraction; do not expand paywalls or challenges.
            await page.evaluate('''() => { for (const e of document.querySelectorAll('body *')) {
                if (['SCRIPT','STYLE','LINK'].includes(e.tagName)) continue;
                const s = getComputedStyle(e);
                if (s.display === 'none' || s.visibility === 'hidden') e.remove();
            }}''')
            content=await page.content()
            sys.stdout.write(content)
        finally:
            await browser.close()
            scratch.cleanup()

if __name__=='__main__':
    asyncio.run(render_worker(sys.argv[2]))
