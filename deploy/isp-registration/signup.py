"""Legacy-browser registration form and private provisioning client."""
from html import escape
import json
import secrets
import socket
import threading
import time

TOKENS = {}
LOCK = threading.Lock()


def form(address, error=''):
    token = secrets.token_urlsafe(32)
    now = time.time()
    with LOCK:
        for key in list(TOKENS):
            if TOKENS[key][1] < now: del TOKENS[key]
        if len(TOKENS) >= 500: TOKENS.pop(next(iter(TOKENS)))
        TOKENS[token] = (address, now+900)
    return ('<P>One account for dial-up PPP, mail, FTP and your personal homepage.</P>'+
            ('<P><B>'+escape(error)+'</B></P>' if error else '')+
            '<FORM METHOD="POST" ACTION="/signup"><INPUT TYPE="HIDDEN" NAME="token" VALUE="'+token+'">'
            '<TABLE CELLPADDING="5"><TR><TD>Account name</TD><TD><INPUT NAME="username" MAXLENGTH="16" SIZE="20"></TD></TR>'
            '<TR><TD>Password</TD><TD><INPUT TYPE="PASSWORD" NAME="password" MAXLENGTH="64" SIZE="24"></TD></TR>'
            '<TR><TD>Password again</TD><TD><INPUT TYPE="PASSWORD" NAME="password2" MAXLENGTH="64" SIZE="24"></TD></TR>'
            '<TR><TD></TD><TD><INPUT TYPE="CHECKBOX" NAME="generate" VALUE="yes"> Generate a password (leave both password boxes empty)</TD></TR>'
            '<TR><TD></TD><TD><INPUT TYPE="SUBMIT" VALUE="Join Xtra ISP"></TD></TR></TABLE></FORM>'
            '<P>Names: 3 to 16 letters, digits, hyphens or underscores, starting with a letter. Passwords: 6 to 64 printable ASCII characters, without spaces, quotes or backslashes.</P>'
            '<P>Use a password just for this retro service. The old clients use plain HTTP, FTP, POP3 and PPP authentication.</P>'
            '<P><B>Mock tariff:</B> NZD 0.07 per KB uploaded + downloaded, plus NZD 0.10 per minute, rounded up. This is a simulation: no payment is due.</P>'
            '<P>Your connection statements go to your new mailbox. Existing mail or web accounts cannot be overwritten here.</P>')


def submit(data, address):
    token = data.get('token', [''])[0]
    with LOCK:
        issued = TOKENS.pop(token, None)
    if not issued or issued[0] != address or issued[1] < time.time():
        return False, form(address, 'This form expired. Please try again.')
    request = {key: data.get(key, [''])[0] for key in ('username','password','password2')}
    request['generate'] = data.get('generate', [''])[0] == 'yes'
    generated = request['generate'] and not request['password']
    if generated:
        request['password'] = request['password2'] = secrets.token_urlsafe(15)
        request['generate'] = False
    recovery = ('<P><B>Save your generated password for a retry:</B> <TT>'+escape(request['password'])+'</TT></P>') if generated else ''
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(60); client.connect('/run/retro-isp-registration/provision.sock')
            client.sendall(json.dumps(request).encode()+b'\n')
            with client.makefile('rb') as response:
                result = json.loads(response.readline(16384))
    except (OSError, ValueError):
        return False, recovery+form(address, 'Registration is temporarily unavailable. Retry with the same name and password if setup had already started.')
    if not result.get('ok'):
        return False, recovery+form(address, result.get('error', 'Registration failed.'))
    user, password = escape(result['username']), escape(result['password'])
    return True, ('<H2>Your account is ready!</H2><P>Save these details now. The password is shown only on this confirmation page.</P>'
                  '<TABLE BORDER="1" CELLPADDING="6"><TR><TD>Username</TD><TD><TT>'+user+'</TT></TD></TR><TR><TD>Password</TD><TD><TT>'+password+'</TT></TD></TR>'
                  '<TR><TD>Dial-up</TD><TD>7920 or 7930; use your username/password above. For multilink use the same number and login on both modems.</TD></TR>'
                  '<TR><TD>Mailbox</TD><TD>'+escape(result['mailbox'])+'</TD></TR><TR><TD>Mail server</TD><TD>mail.net.cryan.nz; POP3 110, IMAP 143, SMTP 25; no TLS, no SMTP authentication on the private network</TD></TR>'
                  '<TR><TD>Personal homepage</TD><TD><A HREF="'+escape(result['homepage'],quote=True)+'">'+escape(result['homepage'])+'</A></TD></TR>'
                  '<TR><TD>FTP</TD><TD>10.150.150.157, port 21, passive mode; same username/password. Upload index.html to your FTP root.</TD></TR></TABLE>'
                  '<P><A HREF="http://mail.net.cryan.nz/mail">Open webmail</A> | <A HREF="/homepages">Visit the neighbourhood</A></P>'
                  '<P>Your mock usage bill is emailed to your mailbox after the whole PPP bundle disconnects. No payment is due.</P>')
