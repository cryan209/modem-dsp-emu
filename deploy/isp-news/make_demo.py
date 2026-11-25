"""Build the reproducible fictional discussion fixture."""
import json
from pathlib import Path

posts = []

def add(key, group, author, subject, body, parent=None):
    posts.append(dict(key=key, group=group if '.' in group else 'xtra.'+group, author=author,
                      subject=('Re: ' if parent else '')+'[demo] '+subject,
                      body=body.strip(), parent=parent))

add('welcome','announcements','Xtra Demo Desk','Welcome to the local newsgroups', '''
Welcome to our fictional little corner of the information superhighway.

These sample conversations are scenery for the retro ISP. The people,
incidents and sale listings are invented. Nobody is actually waiting for
payment, collecting a computer, or investigating the imaginary outages.

Try xtra.test for your first real post. Reply to an existing message to
keep a thread together. Please use plain text and trim quoted replies.
Announcements are read only; the other groups are open for discussion.

The modem noise is real. The office politics below are not.
''')
add('guide-news','help','Mara Packet','GUIDE: reading news without staying online', '''
1. Add the news server 10.150.150.157, port 119.
2. Leave SSL and news-server authentication off on this private service.
3. Fetch the group list and subscribe to xtra.general and xtra.help.
4. Download message bodies before disconnecting, if your reader supports it.
5. Read offline. Compose replies, then reconnect to send them.

Downloading only headers does not necessarily save the articles for offline
reading. Check your reader's download/synchronise options.

Your display name and From address are visible to everyone reading a post.
Use xtra.test to check your setup. There is no public Usenet feed here.
''')
add('guide-news-re1','help','Kev Baud','GUIDE: reading news without staying online', '''
> Download message bodies before disconnecting.

I downloaded only the subjects and spent the evening imagining the answers.
Excellent compression, although the technical accuracy was variable.
''','guide-news')
add('guide-news-re2','help','Mara Packet','GUIDE: reading news without staying online', '''
That is an advanced feature called speculation. For actual answers, ask
for complete articles rather than headers before hanging up. :)
''','guide-news-re1')
add('guide-modem','help','Ruth Handshake','GUIDE: CONNECT speed is not download speed', '''
A 33,600 bit/s modem connection is not a 33,600 byte/s download.
Divide bits by eight first: 4,200 bytes/s before protocol overhead.
Actual file transfer will usually be lower. Line conditions, retries and
the remote server all matter; compressed files rarely compress much more.

For a useful comparison, time a reasonably sized local ZIP download.
Write down the negotiated modem rate and the bytes actually transferred.
A second multilink modem adds capacity only while both links are working.

Do not change several modem settings at once. Keep notes so you can undo
an experiment. Please include your modem model when asking for help.
''')
add('slow','help','Glen Buffer','33.6 modem, downloads around 3 KB/sec. Faulty?', '''
My modem says CONNECT 33600 but the browser says about 3 KB/sec. I expected
33.6 KB/sec. Have I purchased the economy version of the Internet?

Machine is a Pentium 100 with 16 MB and a very determined hard drive.
''')
add('slow-r1','help','Ruth Handshake','33.6 modem, downloads around 3 KB/sec. Faulty?', '''
That sounds plausible for a 33.6 kbit/s link. See the CONNECT-speed guide.
The connection rate is in bits; your browser is reporting bytes.
Try a local ZIP download before changing any modem settings.
''','slow')
add('slow-r2','help','Kev Baud','33.6 modem, downloads around 3 KB/sec. Faulty?', '''
Have you tried holding the mouse closer to the modem? Shorter route for
the packets. No, I have not measured this. Yes, I am being told to stop.
''','slow')
add('slow-r3','help','Glen Buffer','33.6 modem, downloads around 3 KB/sec. Faulty?', '''
Local ZIP is roughly the same speed. The guide explains it, thanks.
Moving the mouse made no difference except knocking over my tea.
''','slow-r2')
add('busy','help','Nicky Retry','Imaginary fault: busy tone at exactly dinner time', '''
Fictional fault report: every time I try to dial at dinner time, somebody
in the house is already on the telephone. Is this a routing problem?

Please advise before the mashed potatoes go cold.
''')
add('busy-r1','help','Mo Exchange','Imaginary fault: busy tone at exactly dinner time', '''
That is household resource contention. The usual fix is agreeing when
the line can be used. A software update cannot create a second phone line.
Do not interrupt an active call to test modem commands.
''','busy')
add('busy-r2','help','Kev Baud','Imaginary fault: busy tone at exactly dinner time', '''
Escalate to the kitchen. Ask for the duty parent. Have your washing-up
reference number ready.
''','busy-r1')
add('mail','help','Tessa Inbox','Mail arrives in browser but not my mail program', '''
Fictional support ticket: I can see a message in webmail, but my desktop
mail program has not fetched it. I put my whole email address in every
box because it seemed thorough. What should I check?
''')
add('mail-r1','help','Mara Packet','Mail arrives in browser but not my mail program', '''
Check the client settings page on the mail service for the exact server,
port and username format. Do not guess SSL/port combinations. Then check
whether the client actually reports a successful fetch.

Please post the error text, but never your password or a screenshot that
shows it. The From address and login name need not have identical formats.
''','mail')
add('mail-r2','help','Tessa Inbox','Mail arrives in browser but not my mail program', '''
The imaginary ticket is resolved: I had configured the outgoing server
where the incoming server should have been. The names were in two boxes,
so statistically I had a fifty-fifty chance.
''','mail-r1')
add('radio-guide','general','DJ Bitbucket','GUIDE: choosing a radio stream for dial-up', '''
Radio page: http://10.150.150.157:8089/

Try the 16 kbit/s mono MP3 first. The number is the audio rate; containers
and network traffic add overhead. Leave room for whatever else you are
doing. If playback repeatedly stops, choose a lower rate or stop downloads.

8 kbit/s MP3 needs MPEG-2.5 support. WMA starts at 24 kbit/s. The RM option
is 8 kbit/s RealAudio; expect telephone-like sound, not hi-fi.
The Listen links launch an external player when the association is set.

Do not assume a 64 kbit/s stream fits a single dial-up modem.
''')
add('radio-r1','general','Pete Cassette','GUIDE: choosing a radio stream for dial-up', '''
At 8 kbit/s I can no longer tell whether it is a guitar solo or my kettle.
Conveniently, I enjoy both.
''','radio-guide')
add('radio-r2','general','DJ Bitbucket','GUIDE: choosing a radio stream for dial-up', '''
The kettle has a wider frequency response. Try 16 or 24 if your link has
room, and let the initial buffer fill before judging it.
''','radio-r1')
add('homepage','general','Jo Underconstruction','GUIDE: your first personal homepage', '''
Create a small file named index.html. A heading and a paragraph will do.
Upload it by FTP to 10.150.150.157 using your personal web-space account.
Your FTP root is the public web directory; do not upload passwords there.

Browse http://10.150.150.157:8086/~yourname/ afterwards, replacing yourname
with the account name. If you use a picture, its filename and letter case
must match the HTML. The account dashboard can edit a small index.html too.

A tiny GIF is kind to dial-up callers. A 2 MB photograph of an UNDER
CONSTRUCTION sign is a philosophical contradiction.
''')
add('homepage-r1','general','Lance Frames','GUIDE: your first personal homepage', '''
Mine will have seven frames, three visitor counters and a scrolling
message explaining that it is optimised for visitors with patience.
''','homepage')
add('homepage-r2','general','Jo Underconstruction','GUIDE: your first personal homepage', '''
Please put the actual page somewhere between the counters.
''','homepage-r1')
add('sounds','general','Nicky Retry','Favourite part of the modem handshake?', '''
The moment just before CONNECT when it sounds as though two fax machines
have finally agreed on who owes whom twenty dollars.

Does anybody else instinctively turn the speaker down after midnight?
''')
add('sounds-r1','general','Mo Exchange','Favourite part of the modem handshake?', '''
I like the silence at the end. It means I may now load a page containing
three small pictures over the next geological period.
''','sounds')
add('sounds-r2','general','Pete Cassette','Favourite part of the modem handshake?', '''
Recorded it once. My flatmate asked whether the washing machine needed
a service. This did not improve relations with either appliance.
''','sounds')
add('ram','retro','Ash Jumper','16 MB or 32 MB for a Win95 box?', '''
Fictional workshop project: Pentium 133, 16 MB, mechanical disk with a
strong work ethic. Would you fit 32 MB before replacing the modem?
Mostly mail, news and staring at a browser progress bar.
''')
add('ram-r1','retro','Ruth Handshake','16 MB or 32 MB for a Win95 box?', '''
If it is constantly swapping, more compatible RAM is likely to make the
machine feel better. It will not change the phone line's connection rate.
Check the motherboard manual: SIMM type, parity and pairing requirements
vary. Do not buy random sticks on optimism alone.
''','ram')
add('ram-r2','retro','Kev Baud','16 MB or 32 MB for a Win95 box?', '''
Put a sticker saying 64 MB on the front. It benchmarks exceptionally well
in photographs.
''','ram')
add('ram-r3','retro','Ash Jumper','16 MB or 32 MB for a Win95 box?', '''
Ordered compatible memory for the fictional machine. Keeping the sticker
idea for the fictional marketing department.
''','ram-r2')
add('jumpers','retro','Bea IRQ','IRQ conflict or just a bad afternoon?', '''
Fictional bench tale: fitted a sound card, serial mouse stopped responding.
Both worked separately. I now have a computer that makes a startup sound
but cannot be persuaded to click anything. Where would you start?
''')
add('jumpers-r1','retro','Mo Exchange','IRQ conflict or just a bad afternoon?', '''
Record the cards' present jumper settings and inspect the assigned I/O
addresses and IRQs. Compare with their manuals before changing anything.
Power down before moving ISA hardware. Try one change, then retest.

The phrase "Plug and Play" is a statement of intent, not an alibi.
''','jumpers')
add('jumpers-r2','retro','Bea IRQ','IRQ conflict or just a bad afternoon?', '''
In this invented case the card had been jumpered onto a serial-port IRQ.
Moved it to a documented free setting. Mouse has returned to management.
''','jumpers-r1')
add('sale-modem','marketplace','Baz Swapmeet','FS: fictional external 33.6 modem, NZ$20', '''
DEMO LISTING - NOT A REAL SALE.

Imaginary external 33.6 modem, beige, LEDs sufficiently bright to navigate
by. Includes matching fictional power supply and serial cable. NZ$20 in
imaginary money; collection from the equally imaginary Packet Bay.

Reason for sale: household has exceeded its agreed modem allocation.
No payment details, real address or actual goods exist for this listing.
''')
add('sale-modem-r1','marketplace','Lance Frames','FS: fictional external 33.6 modem, NZ$20', '''
Does the fictional price include the fictional box? I am only interested
if the box promises "blazing speed" in at least two places.
''','sale-modem')
add('sale-modem-r2','marketplace','Baz Swapmeet','FS: fictional external 33.6 modem, NZ$20', '''
Yes. The imaginary box also says "Internet Ready", which should save you
having to purchase the Internet separately.
''','sale-modem-r1')
add('wanted-simms','marketplace','Ash Jumper','WTB: fictional pair of 72-pin SIMMs', '''
DEMO WANTED AD - NO REAL PURCHASE.

Looking for an imaginary matched pair of 8 MB 72-pin SIMMs for a made-up
Pentium board. Must be compatible with the made-up manual. Will exchange
fictional cash or a fictional shoebox of unlabelled serial adapters.

Please do not send offers or money. This is scenery for the demo feed.
''')
add('wanted-simms-r1','marketplace','Bea IRQ','WTB: fictional pair of 72-pin SIMMs', '''
I have some imaginary 30-pin memory. Could we put two together and file
off the remaining twelve pins?

(This is a joke. Please do not modify real memory modules.)
''','wanted-simms')
add('wanted-simms-r2','marketplace','Ash Jumper','WTB: fictional pair of 72-pin SIMMs', '''
Offer declined by the fictional engineering department and the real laws
of electronics.
''','wanted-simms-r1')
add('free-printer','marketplace','Tessa Inbox','FREE: imaginary dot-matrix printer, enthusiastic', '''
DEMO LISTING - NOTHING TO COLLECT.

Fictional 9-pin printer. Prints beautifully if your definition of beauty
includes a tractor-feed border. Ribbon condition: mostly nostalgia.
Comes with imaginary continuous stationery and no promise of quietness.

Free to a fictional home with understanding fictional neighbours.
''')
add('free-printer-r1','marketplace','Pete Cassette','FREE: imaginary dot-matrix printer, enthusiastic', '''
Could it accompany the 8 kbit/s radio stream? I am starting a band called
Buffer Underrun and we need percussion.
''','free-printer')
add('sale-monitor','marketplace','Jo Underconstruction','SWAP: fictional 14-inch CRT for desk space', '''
DEMO SWAP - NOT A REAL OFFER.

Imaginary 14-inch CRT, displays all sixteen colours of my design judgement.
Seeking imaginary desk space of approximately the same dimensions.
No actual equipment, delivery or collection is involved.

Do not open real CRT equipment; hazardous voltages can remain inside.
''')
add('test','test','Mara Packet','Test thread: can you see this?', '''
This is a fictional sample post, but you can reply with a real test if you
want to check your newsreader. Please do not include passwords or personal
details. The date should be recent and this message should have a subject.

The traditional content of a test post is "test". We support innovation,
including "test 2" and, for advanced users, "is this thing on?".
''')
add('test-r1','test','Kev Baud','Test thread: can you see this?', '''
No.

[This reply demonstrates that the fictional correspondent can see it.]
''','test')
add('test-r2','test','Mara Packet','Test thread: can you see this?', '''
Thank you for the detailed diagnostic report. Escalated to a smaller desk.
''','test-r1')
add('etiquette','announcements','Xtra Demo Desk','A short guide to being excellent on news', '''
Fictional welcome bulletin; the following posting tips are real:

- Keep the useful quoted lines and trim the rest.
- Reply within the thread rather than starting a fresh subject each time.
- Do not publish passwords, private mail or anybody else's personal details.
- Be specific about symptoms, error messages and what you have tried.
- Say when a suggestion fixed the problem; the next reader will thank you.
- Plain text makes old newsreaders and slow links much happier.

All seeded buy/sell adverts are fictional and are not offers to transact.
The comic replies are not technical instructions.
''')

from more_demo import extend
extend(add)

from history_demo import extend as extend_history
extend_history(add)
from to_200 import extend as extend_to_200
extend_to_200(add)
from cupholder_demo import extend as extend_cupholders
extend_cupholders(add)
from bandwidth_demo import extend as extend_bandwidth
extend_bandwidth(add)
from quake_demo import extend as extend_quake
extend_quake(add)
from nz_general_demo import extend as extend_nz
extend_nz(add)
from thousand_demo import extend as extend_thousand
extend_thousand(add)
from flame_demo import extend as extend_flames
extend_flames(add)
from longform_demo import extend as extend_longform
extend_longform(add)
from naturalize_demo import clean
clean(posts)
from devlogs import extend as extend_devlogs
extend_devlogs(posts)
from more_devlogs import extend as extend_more_devlogs
extend_more_devlogs(posts)
from general_spelling import apply as apply_spelling
apply_spelling(posts)

keys = set()
for post in posts:
    assert post['key'] not in keys
    assert post['parent'] is None or post['parent'] in keys
    keys.add(post['key'])
Path(__file__).with_name('demo-posts.json').write_text(json.dumps(posts, indent=2)+'\n')
print(len(posts), 'news articles generated')
