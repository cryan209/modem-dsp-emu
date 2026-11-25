"""Second batch: fourteen new fictional conversations (56 articles)."""
def extend(add):
    def thread(key, group, subject, authors, bodies):
        add(key, group, authors[0], subject, bodies[0])
        add(key+'-r1', group, authors[1], subject, bodies[1], key)
        add(key+'-r2', group, authors[2], subject, bodies[2], key)
        add(key+'-r3', group, authors[0], subject, bodies[3], key+'-r1')

    thread('ftp-case', 'help', 'My homepage works, but the picture is missing',
           ['Fern Filename','Jo Underconstruction','Kev Baud'], [
'''Fictional support ticket: uploaded index.html and LOGO.GIF. The HTML asks
for logo.gif. It works when I open the page on my old Windows machine,
but the server shows a broken picture. Have I worn the GIF out?''',
'''Use the exact same spelling and letter case in the HTML and uploaded
filename. The server can distinguish LOGO.GIF from logo.gif even if your
local filesystem does not. Also check that the image is in the directory
your relative link points to. Keep a local copy before renaming files.''',
'''The picture is probably still developing. Leave the page in a dark room
for twenty minutes. This advice has not survived peer review.''',
'''Matching the case fixed this fictional ticket. The logo is now visible
and unfortunately confirms that I chose purple text on a lime background.
A separate design incident has been opened.'''])

    thread('mail-attachment', 'help', 'Why did a small photo turn into a giant email?',
           ['Glen Buffer','Mara Packet','Lance Frames'], [
'''Imaginary mail question: attached a camera photo and everyone says it
takes ages to collect. It looked quite small on my screen. Does resizing
the window make the attachment smaller?''',
'''The display size and file size are different. Resize a copy of the
actual picture in an image editor and save at suitable JPEG quality;
check the resulting file size before attaching it. Email encoding can
increase the transmitted size too. Keep the original if you need it.''',
'''Put "small photo" in the subject line. The mail server should respect
your artistic intent. Mine has declined to comment.''',
'''Saved a genuinely smaller copy in the fictional exercise. It is now
possible to receive the picture before the subject of the picture has
left university. Thanks for explaining the difference.'''])

    thread('two-modems', 'help', 'Second modem connected: why is one file still slow?',
           ['Owen Bundle','Ruth Handshake','Kev Baud'], [
'''Fictional multilink ticket: both modems have their CONNECT lights on,
but the download is erratic. Is two lights enough evidence that I have
doubled the useful speed? I have counted them twice to be sure.''',
'''Not by itself. Check that both links joined the same bundle and look at
the actual bytes delivered over time. A link can be connected yet lose
traffic or stop responding. Record the initial rates, test a local file,
and compare one link with two rather than guessing from the lights.''',
'''Add two more LEDs to the case. The graph will become much more
convincing. This is decorative advice, not network engineering.''',
'''For this invented case the second member was stalling. One-link tests
were steadier. Logging rates and useful bytes made the comparison much
clearer than staring at the front panel until it felt faster.'''])

    thread('cache-refresh', 'help', 'I edited my page, but the old heading is still there',
           ['Fern Filename','Jo Underconstruction','Pete Cassette'], [
'''Imaginary web-space mystery: uploaded a new index.html, and my browser
still shows yesterday's heading. I have accused the FTP program twice.
What should I check before accusing it a third time?''',
'''Check the exact URL and uploaded filename first. Then reload the page
and check the browser cache settings; old browsers may reuse a cached
copy. Compare the page source with your local file. If other people can
see the update, that is a useful clue that the upload succeeded.''',
'''Yesterday's heading has seniority. New headings must complete a
probation period before being displayed.''',
'''The fictional file was uploaded correctly. Reloading without the stale
cached copy showed the new heading. I have apologised to FTP and am now
considering a small gift basket for the browser.'''])

    thread('radio-buffer', 'general', 'Radio and a download: negotiating custody of the modem',
           ['Nicky Retry','DJ Bitbucket','Pete Cassette'], [
'''Imaginary listening experiment: 32 kbit/s radio is fine until I download
something else, then the music pauses. Is the station taking dramatic
breaths, or have I asked the line to do too much?''',
'''Try 16 kbit/s first and leave some capacity for the download. Audio and
file transfers share the same connection, and overhead needs room too.
Let the initial player buffer fill. If it keeps stopping even without a
download, compare a lower rate and check the underlying link.''',
'''I prefer the gaps. They let me contribute my own lyrics, which explains
why the household has requested a much larger buffer.''',
'''In the fictional test, 16 kbit/s was steadier while browsing. A large
download could still crowd it out. Apparently bandwidth cannot be created
by ticking "high quality" with sufficient confidence.'''])

    thread('web-rings', 'general', 'Proposal: a ring of gloriously small homepages',
           ['Jo Underconstruction','Lance Frames','Mara Packet'], [
'''Fictional community proposal: everyone makes one small homepage with a
few favourite links, a short introduction and perhaps a modest GIF.
Could we put previous/next links on them and call it a web ring?''',
'''I have designed a 400 KB navigation button. It says "small" in animated
letters. I am prepared to negotiate it down to three animations.''',
'''Start with ordinary text links. They work in old browsers and are cheap
to fetch. Make sure the linked sites actually exist before distributing
the ring code. The fictional proposal is not an announcement of a live
registration service.''',
'''Text links win. The hypothetical ring remains a proposal, but I now
have an excellent imaginary committee and only two imaginary subcommittees.
That is a good result by Internet standards.'''])

    thread('quote-trim', 'general', 'Do I need to quote the entire thread when replying?',
           ['Tessa Inbox','Mara Packet','Kev Baud'], [
'''Invented etiquette question: my reply is one line, but the quoted text
fills six screens. Should I keep every previous signature so future
historians understand the context?''',
'''Keep the relevant lines and trim the rest, especially repeated
signatures. Put your reply near the text it answers. References headers
keep the thread relationship; copying the whole conversation is not what
makes threading work. Be careful not to change somebody's meaning.''',
'''Please quote this reply, all previous replies, and your entire hard
drive inventory. I am trying to slow the newsgroup down to walking pace.''',
'''Trimmed my fictional reply to the two lines I was answering. Historians
can consult the original articles. The scroll bar has returned to a size
that can be manipulated by an ordinary human hand.'''])

    thread('serial-cable', 'retro', 'Found a serial cable. How do I tell what it is?',
           ['Bea IRQ','Mo Exchange','Baz Swapmeet'], [
'''Fictional workbench discovery: a cable in the box marked MISC. One end
fits a serial port. The other end also fits a serial port. Does that make
it a null-modem cable, or simply a very optimistic straight cable?''',
'''Connector shape alone is not enough. Find its markings or check the
pin connections against the equipment manuals using a continuity tester
with the cable disconnected. Straight and null-modem wiring serve
different purposes; adapters can add another layer of confusion.''',
'''The MISC label is the industry standard for "will become essential
approximately one week after you throw it away".''',
'''In the fictional exercise it was straight-through, with a null-modem
adapter hiding elsewhere in the same box. I have labelled both. The box
has objected that this is contrary to its founding principles.'''])

    thread('ball-mouse', 'retro', 'Mouse only travels east. Has it chosen a career?',
           ['Ash Jumper','Bea IRQ','Kev Baud'], [
'''Imaginary repair bench: ball mouse moves horizontally but vertical
movement is unreliable. Is it a driver issue or should I inspect the
mechanism before reinstalling everything in sight?''',
'''With the machine off, check the removable ball and rollers according
to the mouse's instructions. Built-up lint can stop a roller turning.
Avoid soaking the electronics or forcing small clips. If cleaning does
not help, test another mouse before making software changes.''',
'''It has been promoted to spreadsheet work and only accepts tasks in
columns. Ask human resources about vertical development opportunities.''',
'''The fictional roller had accumulated enough lint to qualify as a small
mammal. Cleaned gently, and the pointer now moves in both directions.
No driver ceremony was required.'''])

    thread('disk-noise', 'retro', 'Old disk has started clicking: what first?',
           ['Owen Bundle','Ruth Handshake','Pete Cassette'], [
'''Fictional preservation question: an old hard disk has started making
new clicking noises. The files are backed up only in the sense that I
have been thinking about backing them up. What is the sensible next step?''',
'''New noises do not establish the exact fault, but prioritise recovering
important data rather than benchmarking or defragmenting the disk. Avoid
unnecessary writes. If data is irreplaceable or the drive is failing to
read, stop and consider professional recovery rather than repeated tests.
Do not open the drive or rely on folklore repairs.''',
'''I was going to suggest recording it for percussion, but even I think
the backup should come first. My band can use the imaginary printer.''',
'''In this invented scenario I stopped experimenting and made a recovery
plan. The lesson is that "I will copy it next weekend" is not a storage
format, however widely deployed it may be.'''])

    thread('crt-purchase', 'marketplace', 'FS: imaginary 15-inch CRT, NZ$35 in pretend currency',
           ['Baz Swapmeet','Lance Frames','Jo Underconstruction'], [
'''DEMO LISTING - NOT A REAL SALE.
Fictional 15-inch CRT, beige, includes an imaginary VGA lead. NZ$35 in
pretend money. No real item, collection address or payment arrangement.
Heavy enough in the story to require rearranging the fictional desk.
Do not open actual CRT equipment; dangerous voltages may remain inside.''',
'''Can the fictional monitor display my website's background at full
intensity? It is a shade I have named Emergency Lime.''',
'''Only from the imaginary garden, using a very long imaginary cable.
The fictional neighbours have asked you to close the blinds.''',
'''Imaginary sale still imaginary. No real offers or payments please.
Colour performance guaranteed only by the fictional marketing department,
whose membership has just been reviewed.'''])

    thread('wanted-keyboard', 'marketplace', 'WTB: fictional keyboard without the biscuit collection',
           ['Tessa Inbox','Bea IRQ','Kev Baud'], [
'''DEMO WANTED AD - NO REAL PURCHASE.
Seeking an imaginary AT-style keyboard for a fictional restoration.
Prefer one with all keys present and fewer than three former lunches
inside. No actual exchange, address or money is involved.''',
'''Imaginary offer: one keyboard with a five-pin DIN plug. Check the
actual machine's connector and electrical/interface requirements before
buying a real adapter; matching plastic is not the whole story.''',
'''Mine includes a complimentary raisin. This raises the fictional asking
price because it is now technically a keyboard and pantry combination.''',
'''Fictional offer noted. The pretend restoration will first consult its
pretend manual. The raisin has been referred to the fictional auctioneer
as a separate lot.'''])

    thread('free-magazines', 'marketplace', 'FREE: imaginary stack of computer magazines',
           ['Pete Cassette','Ash Jumper','Baz Swapmeet'], [
'''DEMO LISTING - NOTHING TO COLLECT.
Imaginary stack of late-90s computer magazines. Cover discs are fictional
and may or may not contain fictional shareware. Free to a nonexistent
reader with a nonexistent shelf. No real delivery or collection.''',
'''Does the imaginary issue explain how the next CPU will make all of our
current computers obsolete by lunchtime? I need to complete my set.''',
'''Every issue does. One says that by next year all shopping will happen
in three-dimensional virtual malls. I am keeping it for the predictions.''',
'''The fictional stack is provisionally reserved for an imaginary person.
Please do not make actual travel plans. The imagined cover disc remains
unread until someone locates an imagined CD-ROM driver.'''])

    thread('test-wrap', 'test', 'Testing quotes, apostrophes and the mysterious dot',
           ['Fern Filename','Mara Packet','Kev Baud'], [
'''Fictional test article for the web reader:

> This is a quoted line.
It's possible to write an apostrophe without summoning a wizard.
.dot at the beginning of a line
<This is plain text, not an HTML tag.>

If you can read these, your viewer has found something to do.''',
'''The web reader should show angle brackets as text and preserve the
leading dot. NNTP uses dot-stuffing on the wire; the reading client removes
the extra protocol dot. No wizard required, but a working parser helps.''',
'''I can confirm that the mysterious dot is round from this distance.
Will report again after enlarging the font.''',
'''Fictional test complete. The text is visible and the dot has been
promoted to punctuation. I will stop posting tests now, subject to the
usual exception for one final test.'''])
