"""Seventeen more local conversations: 68 articles."""
def extend(add):
    threads = [
('dns-numbers','help','Numbers work, names do not',('Mark Ellis','Denise Carter','Kev Baud'),[
'''Can reach a page by its numeric address, but names fail. Reconnecting
sometimes helps. Have I configured the wrong sort of TCP/IP?''',
'''Check which DNS servers the dial-up connection receives and whether
name lookups actually succeed through them. Record the settings before
changing anything. Being able to reach an IP narrows the problem, but
does not mean every other part of the connection is correct.''',
'''Computers only understand numbers. Names were a concession to people
who cannot remember the telephone number of the computer shop.''',
'''The connection had an old DNS setting carried over from another setup.
Corrected that, reconnected, and names now work. Will label the old notes
rather than leaving them beside the current ones. Cheers.''']),
('resume-file','help','Download stopped at 98 percent. Must I start again?',('Fern Filename','Mara Packet','Glen Buffer'),[
'''Large installer nearly finished, then the connection dropped. There is
a partial file on disk. Is there a way to get just the missing bit, or
does the telephone company get another hour of my life?''',
'''It depends on your downloader and the server. Some support resuming a
partial transfer. Keep the partial file, look for a resume option, and
check the final size or supplied checksum afterwards. A nearly complete
installer is not a complete installer.''',
'''Ninety-eight percent is where my downloads traditionally become a
family event. Everybody has to stop looking at the telephone.''',
'''The download manager resumed it. Checked the completed file rather than
trying to run the fragment. Keeping the manager; retiring the ritual of
promising not to touch anything until the progress bar finishes.''']),
('wrong-clock','help','My replies appear before the messages I am answering',('Colin R','Mara Packet','Nicky Retry'),[
'''Newsreader has put my latest reply above yesterday's question. Have I
discovered a faster connection, or is the date on this PC important?''',
'''Check the local date, time and time zone, and whether the clock stays
correct after power off. Readers may sort by date, while threading uses
message relationships. A wrong clock can make a perfectly ordinary
conversation look like correspondence from the future.''',
'''If you really can answer before the question, please post tomorrow's
modem lottery results. Failing that, the weather will do.''',
'''Clock was a month out. Corrected it and made a note to check it after
shutdown. My career as a time traveller was brief but administratively
complicated.''']),
('ftp-mode','help','Text file has odd line breaks after FTP',('Jo Underconstruction','Denise Carter','Lance Frames'),[
'''Uploaded a text file and the line breaks look different on the other
machine. Which transfer mode should I use? I usually click the option
that sounds most modern.''',
'''For images, ZIPs and executables, use binary transfer so the bytes are
preserved. Text-mode transfer can change line endings. For a text file,
choose based on what the destination expects; if in doubt, keep an
original and compare a small test file first.''',
'''My client says AUTO, which I interpret as "someone else has agreed to
be responsible". So far no one has answered my letters.''',
'''Tested with a copy and found the client changing the endings in text
mode. Binary preserved the file I wanted. Also checked my GIF uploads;
they were already binary, so one mystery at a time.''']),
('mail-double','help','Two PCs, one mailbox, twice the confusion',('Tessa Inbox','Mara Packet','Mark Ellis'),[
'''Read mail on the desktop, then the laptop says the inbox is empty.
Would like both to see it. Is the second machine connecting too slowly?''',
'''Check whether the first client removes messages from the server after
fetching them. If you choose to leave copies, understand how and when
they will eventually be removed, or the mailbox can accumulate old mail.
Back up important messages before experimenting with client settings.''',
'''I tried solving this by forwarding everything to myself. Can confirm
that it did not produce a simpler diagram.''',
'''The desktop was removing messages. Changed the retention behaviour and
tested with an unimportant message. Both machines now collect it. Will
keep a proper local archive rather than use the server as a cupboard.''']),
('cookie-login','help','Account page signs me out when I return later',('Nicky Retry','Denise Carter','Kev Baud'),[
'''Can sign in, read the account page, then return much later and have to
sign in again. Browser accepts cookies. Is this a fault?''',
'''Not necessarily. Sessions can expire, and a service restart can also
end them. Use a fresh sign-in form and check whether it works normally
straight afterwards. Do not post your password or session cookie here.
If it fails immediately, the exact error text would help.''',
'''The website has concluded that you have gone outside. A serious breach
of the subscriber agreement, at least in this weather.''',
'''It works immediately; I was coming back after a long break. Will stop
keeping the sign-in form open all afternoon as though it were a reserved
seat. Nothing else appears broken.''']),
('gif-jpeg','general','GIF or JPEG for the homepage?',('Fern Filename','Jo Underconstruction','Pete Cassette'),[
'''One photo, one little logo. Is it worth converting everything to GIF
so the page has only one kind of picture?''',
'''Try JPEG for the photo and GIF for the small flat-colour artwork.
Compare actual file sizes and appearance instead of converting merely
for consistency. Keep originals, and do not repeatedly resave the only
copy while trying quality settings.''',
'''My logo has so few colours that the printer offered to colour it in
by hand. Still larger than the entire text of the page, somehow.''',
'''The photo is much smaller as JPEG. The logo looks cleaner as GIF.
Removed an enormous unused border too. It is embarrassing how much
bandwidth I was spending on a rectangle of nothing.''']),
('guestbook-question','general','Guestbook etiquette: signing your own guestbook',('Lance Frames','Mara Packet','Kev Baud'),[
'''If I install a guestbook and nobody signs it, may I leave the first
entry myself? Otherwise it looks as though the site opened during a
power cut.''',
'''A welcome entry from the site owner is perfectly understandable. Do
not invent independent endorsements. A short introduction and clear
contact link can be more useful than a guestbook anyway.''',
'''I sign mine "the management". There is only one of me, but we like to
maintain standards.''',
'''Have added a welcome instead of pretending to be my own delighted
visitor. The management wishes to thank the management for implementing
this policy so promptly.''']),
('irc-nick','general','Somebody already has my IRC nickname',('Andy North','Mo Exchange','Baz Swapmeet'),[
'''Tried joining IRC with the usual nickname and the server says it is
in use. Is it my old browser connection still sitting there?''',
'''Could be an existing connection, or somebody else using the same name.
Try another nickname first. If your previous connection ended abruptly,
allow time for it to disappear. A familiar nickname is not proof that
the person behind it is the same person.''',
'''I use a nickname nobody else would want. Unfortunately it is now my
nickname and the strategy has become a personal problem.''',
'''Old connection vanished and I got the name back. Have picked a spare
for next time. Thanks for the reminder about names; will not treat a
nickname as a password or an identity check.''']),
('frames-bookmark','general','Bookmark takes me to the wrong part of a framed site',('Mark Ellis','Jo Underconstruction','Lance Frames'),[
'''Bookmarked a useful page, returned later and got the front page instead.
The site has frames. Did the bookmark forget where I had been?''',
'''You may have bookmarked the outer frameset rather than the document
shown inside it. Look for a direct link to the actual page, or use your
browser's options for opening that frame separately. Authors can help
by providing ordinary links as well as framed navigation.''',
'''Frames are useful because they let four separate documents disagree
about which one is the actual page.''',
'''Found the direct document URL and that bookmark works. Sent the author
a polite note asking for a direct link. Did not include my first draft,
which was mainly punctuation.''']),
('serial-port','retro','COM port opens in terminal but not in dial-up',('Bea IRQ','Ruth Handshake','Kev Baud'),[
'''Terminal talks to the modem. Dial-Up Networking says it cannot open
the port. Terminal is still running because I wanted to watch the lights.
Have I found a driver conflict?''',
'''Close the terminal and any other program that may have the port open,
then try DUN again. Test one application at a time before changing IRQs
or drivers. If it still fails, note the exact port and error message.''',
'''Only one person may hold the imaginary steering wheel at a time.
The modem has regrettably rejected our proposal for a committee.''',
'''Closing the terminal fixed it. I had left it connected to the port
while asking another application to use that same port. No hardware
changes needed. Leaving this here as a warning to my future self.''']),
('cd-audio','retro','CD-ROM plays audio CDs but not this data disc',('Ash Jumper','Bea IRQ','Colin R'),[
'''Drive will play a music CD, but one data disc refuses to read. Before
I buy a new drive, what is the sensible comparison?''',
'''Try a known-good pressed data disc and test the suspect disc in a
second drive. Audio playback alone does not establish that every data
reading path works. Inspect the disc without abrasive experiments and
record whether the failure affects one disc or all data discs.''',
'''Please identify the disc type too. "It is silver" has so far failed
to narrow down most of the collection.''',
'''Pressed discs work. The troublesome one also fails elsewhere. Will
replace that disc rather than the drive. Saved myself a shopping trip
and acquired a circular object for the box of unresolved questions.''']),
('bios-ram','retro','BIOS sees the new memory, Windows feels much the same',('Owen Bundle','Denise Carter','Pete Cassette'),[
'''Added RAM. BIOS counts it correctly, but a web page still takes ages
to arrive. Was expecting a more dramatic result for the screwdriver work.''',
'''More memory helps when the machine was short of it; it does not raise
the modem's line rate. Compare local application switching and disk
activity separately from time spent waiting for network transfers.
Keep the test workload similar before judging the upgrade.''',
'''The extra memory is occupied by the expectation that the upgrade will
make everything faster. Very demanding application, that one.''',
'''Switching between programs is better and there is less disk thrashing.
The download still arrives at modem speed, as it apparently has every
right to do. Calling the upgrade a success with revised expectations.''']),
('screen-font','retro','800x600 is readable, 1024x768 is impressive',('Lance Frames','Jo Underconstruction','Andy North'),[
'''Can make the old monitor show 1024x768, but the text is tiny. Is the
correct approach to sit nearer, change fonts, or stop showing off?''',
'''Choose a mode the monitor supports comfortably and which you can
actually read. Check its specifications before changing refresh rates.
Use suitable font settings rather than making every site use enormous
fixed-size text. More pixels are not automatically more usable.''',
'''Stop showing off. Keep a screenshot of the larger desktop to prove
that it happened, then return to being able to read the clock.''',
'''Back to a comfortable supported mode. Can now read error messages
without leaning over the keyboard, which has improved both diagnosis
and the condition of my nose.''']),
('sale-scanner','marketplace','FS: flatbed scanner and a frightening amount of software',('Baz Swapmeet','Fern Filename','Bea IRQ'),[
'''Flatbed scanner with interface card and driver disks. $40 ono. Was
working before the last desk rearrangement; will test again before any
agreement. Manual included, in a folder marked something unrelated.

Fictional listing.''',
'''Which interface and exact model? Also need to know whether the driver
disks match the machine I would use, not just whether the scanner lights
up attractively.''',
'''Please keep the interface card with it. "Industry standard connector"
on the box is not always the happy ending one hopes for.''',
'''Will add the model and card details after checking the labels. Keeping
the whole lot together. The folder has been renamed SCANNER, an action
which may invalidate its natural camouflage.''']),
('wanted-drive','marketplace','WTB: small IDE drive for a 486 restoration',('Colin R','Ash Jumper','Kev Baud'),[
'''Looking for a small IDE hard disk for a 486. Quiet and tested preferred.
Do not need huge capacity; mostly after something the BIOS understands.
Will check the board details before agreeing on a model.

Fictional listing.''',
'''Have one in a parts box but will identify and test it before offering
it. Please send the board model first; I would rather spend five minutes
checking than have it make a round trip for nothing.''',
'''I have one that is exceptionally quiet when unplugged. Cannot yet
claim this as a meaningful performance advantage.''',
'''Board details sent. No rush; testing first is appreciated. Restoration
has already waited twenty years and can probably manage another evening.''']),
('test-reply','test','Testing thread view after changing newsreaders',('Nicky Retry','Mara Packet','Glen Buffer'),[
'''Changed newsreaders and this is my first post from the new one. Does
the subject look normal? It offered to add formatting and I told it no.''',
'''Subject and plain text look fine. Replying in the same thread so you
can check whether it appears underneath the original message.''',
'''Also visible in the web viewer. No unusual colours, which is a shame
because I was hoping to blame the monitor again.''',
'''Replies are grouped correctly. Have found the setting that stops it
quoting the entire signature as well. Will return to lurking now that
the new software has passed its entrance examination.''']),
]
    assert len(threads)==17
    for key,group,subject,authors,bodies in threads:
        add(key,group,authors[0],subject,bodies[0])
        add(key+'-r1',group,authors[1],subject,bodies[1],key)
        add(key+'-r2',group,authors[2],subject,bodies[2],key)
        add(key+'-r3',group,authors[0],subject,bodies[3],key+'-r1')
