"""Small text-report uploads for the Win95 lab, with no public file listing."""
import hashlib
import json
import ntpath
import re
import threading
import uuid
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from html import escape
from pathlib import Path
from urllib.parse import parse_qs

ROOT=Path('/var/lib/xtra-registration/reports')
CLIENTS={f'10.254.{n}.2' for n in range(91,95)} | {'10.254.104.2','10.254.104.6','127.0.0.1','::1'}
LIMIT=256*1024
LOCK=threading.Lock()
FORM='''<HTML><HEAD><TITLE>Upload PCI Report</TITLE></HEAD><BODY BGCOLOR="#FFFFFF">
<H1>Upload PCI Report</H1><P><A HREF="http://192.168.1.100/">Gateway</A> |
<A HREF="http://192.168.1.100/software">Tools downloads</A></P>
<P>In MS-DOS mode, run <B>LISTPCI &gt; PCIIDS.TXT</B>. Return to Windows,
connect, then choose that file below.</P>
<FORM METHOD="POST" ACTION="/upload" ENCTYPE="multipart/form-data">
<P>Report file: <INPUT TYPE="FILE" NAME="report" SIZE="30"></P>
<P><INPUT TYPE="SUBMIT" VALUE="Upload report"></P></FORM>
<H2>Or paste the report</H2><FORM METHOD="POST" ACTION="/upload">
<TEXTAREA NAME="text" COLS="60" ROWS="10"></TEXTAREA>
<P><INPUT TYPE="SUBMIT" VALUE="Send pasted report"></P></FORM>
<P>Text reports only (.TXT, .LOG or .CSV), up to 256 KiB. Reports are saved
on eicon420 for diagnosis. After uploading, tell me in the chat.</P></BODY></HTML>'''

def receive(content_type, body, client):
    if content_type.lower().startswith('multipart/form-data'):
        message=BytesParser(policy=policy.default).parsebytes(b'Content-Type: '+content_type.encode('ascii')+b'\r\nMIME-Version: 1.0\r\n\r\n'+body)
        if not message.is_multipart():raise ValueError('Invalid upload form.')
        parts=[p for p in message.iter_parts() if p.get_param('name',header='content-disposition')=='report']
        if len(parts)!=1:raise ValueError('Choose one report file.')
        part=parts[0];name=ntpath.basename(part.get_filename() or '')
        if Path(name).suffix.lower() not in ('.txt','.log','.csv'):raise ValueError('Choose a .TXT, .LOG or .CSV text report.')
        data=part.get_payload(decode=True) or b''
    elif content_type.lower().startswith('application/x-www-form-urlencoded'):
        values=parse_qs(body.decode('ascii',errors='replace'),encoding='utf-8',errors='replace')
        data=values.get('text',[''])[0].encode('utf-8');name='pasted-report.txt'
    else:raise ValueError('Use the upload or paste form.')
    if not data.strip():raise ValueError('The report is empty.')
    if len(data)>LIMIT:raise ValueError('The report exceeds 256 KiB.')
    if b'\x00' in data or sum(b<32 and b not in (9,10,12,13,26) for b in data)>len(data)//20:
        raise ValueError('This appears to be a binary file. Upload the text report, not LISTPCI.EXE.')
    try:text=data.decode('utf-8-sig')
    except UnicodeDecodeError:text=data.decode('cp437')
    report_id=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:12]
    metadata=dict(id=report_id,original_name=name,client=client,bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),uploaded_at=datetime.now(timezone.utc).isoformat())
    with LOCK:
        ROOT.mkdir(parents=True,mode=0o700,exist_ok=True)
        if sum(p.stat().st_size for p in ROOT.iterdir() if p.is_file())+len(data)>10*1024*1024:
            raise ValueError('Report storage is full. Ask in chat to clear old reports.')
        target=ROOT/(report_id+'.txt')
        with target.open('xb') as output:output.write(data)
        target.chmod(0o600)
        info=ROOT/(report_id+'.json');info.write_text(json.dumps(metadata,indent=2));info.chmod(0o600)
    # Escape all report text: uploads are data, never page markup or instructions.
    return ('''<HTML><HEAD><TITLE>Report uploaded</TITLE></HEAD><BODY BGCOLOR="#FFFFFF">
<H1>Report uploaded</H1><P>Saved '''+escape(name)+''' ('''+str(len(data))+''' bytes).</P>
<P>Report ID: <B>'''+report_id+'''</B></P><P>Tell me in the chat that the report is uploaded.</P>
<P><A HREF="/upload">Upload another report</A></P><H2>Report preview</H2><PRE>'''+escape(text[:32000])+'''</PRE></BODY></HTML>''').encode('ascii',errors='xmlcharrefreplace')
