"""Original conversations inspired by topics in 1995-97 Usenet archives."""
def extend(add):
    def thread(key, group, subject, authors, bodies):
        add(key,group,authors[0],subject,bodies[0])
        add(key+'-r1',group,authors[1],subject,bodies[1],key)
        add(key+'-r2',group,authors[2],subject,bodies[2],key)
        add(key+'-r3',group,authors[0],subject,bodies[3],key+'-r1')

    thread('winsock-upgrade','help','Netscape 16 bit works, 32 bit refuses to connect',
           ['Mark Ellis','Denise Carter','Pete Cassette'],[
'''Upgraded the 486 from Windows 3.11 last weekend. Old Navigator connects
through Trumpet, new Navigator says it cannot find anything. Same modem,
same phone number. Is the 32 bit version fussier about phone lines?''',
'''Start by checking which networking stack each program is using. The
older setup and Win95 Dial-Up Networking are not interchangeable merely
because both show a connection. Try the native DUN connection with the
32 bit browser. Note your existing setup before changing it.''',
'''This is why I upgrade one application every six months. Gives me five
months to forget what I did to make the previous one work.''',
'''Connected using DUN first and the new browser works. The modem was
innocent. Will sort out the older programs before removing anything.
Thanks Denise. Posting this so the next person finds the answer.'''])

    thread('autodial','help','Old mail utility will not bring up the dialler',
           ['Colin R','Denise Carter','Glen Buffer'],[
'''Small 16 bit mail utility works once I am online, but unlike the browser
it does not start the Win95 dialler. Is this a server setting?''',
'''Probably look at the application and local autodial behaviour first.
If opening DUN manually makes it work, the server connection itself is
not the missing step. Old applications do not all trigger autodial the
same way. Which version of the utility is it?''',
'''My solution is to connect before opening it. Not elegant, but neither
is the laminated list of other things I must do in the correct order.''',
'''Manual connection works consistently. Version is ancient enough that
the help file congratulates me on owning a mouse. I can live with the
extra step until I find an update.'''])

    thread('modem-log','help','28800 feels faster than 33600 -- not a typo',
           ['Andy North','Ruth Handshake','Kev Baud'],[
'''Borrowed a second modem. Higher CONNECT number, worse file transfers.
Tested the same ZIP twice. Anyone seen this or am I timing the cache?''',
'''Save the connection logs for both tests and compare error correction,
retries and the actual file bytes transferred. Also make sure both tests
really fetch the file. A high initial rate does not guarantee a clean
sustained connection; do not blame compression without checking the logs.''',
'''Perhaps 33600 is the speed at which it promises to deliver the bytes,
and 28800 is the speed at which it actually remembers to do so.''',
'''Retested with logs. Different error-control reports, plus the quicker
CONNECT was much less steady. Keeping the reliable setup for now.
The stopwatch did better than the box artwork.'''])

    thread('null-ras','retro','Direct cable connection is not talking to NT RAS',
           ['Colin R','Mo Exchange','Bea IRQ'],[
'''Trying a serial link between the spare Win95 PC and an NT machine.
Cable worked in an older setup. Direct Cable Connection gets nowhere.
Both ports are set to 57600, so what am I overlooking?''',
'''Matching port speeds only addresses one part of it. Check the cable
wiring and whether the services on both ends expect compatible framing
and connection setup. Direct Cable Connection is not a promise that it
will talk to any serial service called RAS.''',
'''Please list the actual server version and cable type. "Same as before"
is not enough to tell us whether the software at the two ends agrees.
Also check flow control; do not just buy another cable at random.''',
'''Fair point. I had treated two different connection methods as synonyms.
Will write down the actual combination and start again. Cable has been
returned to the box marked NOT YET PROVEN GUILTY.'''])

    thread('html-war','general','Best viewed with... something other than my browser',
           ['Lance Frames','Jo Underconstruction','Mark Ellis'],[
'''Put a visitor counter and a scrolling heading on my page. Looks fine
here. A friend says the heading is missing and the table is wider than
his screen. Must I test every browser anybody ever wrote?''',
'''You can start by making the essential content readable without the
extensions. Test another browser and a narrow window. A page that loses
its decoration is one thing; a page that loses its navigation is another.''',
'''I am less worried about which browser wins than whether I can find the
phone number before the background finishes arriving.''',
'''Removed the scrolling heading and used ordinary links for navigation.
Counter remains because my mum has visited twice and I want evidence.
The page is now readable in the other browser too.'''])

    thread('inf-speed','retro','Will a new modem INF turn my 28.8 into a 33.6?',
           ['Andy North','Bea IRQ','Baz Swapmeet'],[
'''Found a newer INF with 33600 in the filename. My modem is sold as 28.8.
Is that an upgrade or just Windows learning a new name for it?''',
'''An INF describes the modem to Windows; it does not by itself replace
the modem firmware or hardware. Check the exact model and the vendor's
upgrade documentation. A similar case and a bigger number in a filename
are not enough to establish compatibility.''',
'''I have a label maker if that helps. Can offer 33.6, 56K or "experimental
fibre" depending on how much space is available on the front panel.''',
'''Model number checked. This file is for a different revision. Have left
it alone. The label maker may still be useful for identifying the power
supplies, which are apparently breeding.'''])

    thread('ascii-signature','general','How long is too long for a signature?',
           ['Fern Filename','Mara Packet','Kev Baud'],[
'''Trying out a signature with the machine specifications, modem settings,
favourite quotation and a small ASCII dragon. It is sixteen lines.
Too much? The dragon alone accounts for half.''',
'''A short signature makes threads easier to read, particularly on small
screens. Put the full machine details in posts where they help diagnose
the problem. You do not need your IRQ assignments under a recipe.''',
'''Keep the dragon. Remove the message. Most of us are here for the dragon
by this stage anyway.''',
'''Down to three lines. The dragon has moved to the homepage, where it has
room to spread out and frighten the visitor counter.'''])

    thread('bundle-sale','marketplace','FS: 486 board, CPU and RAM, $60 ono',
           ['Baz Swapmeet','Ash Jumper','Andy North'],[
'''486 motherboard with DX2/66 and 8 MB. Came from a working desktop last
month. No case or disk. Can include the printed jumper sheet. Asking $60
ono, would consider a decent external modem in part exchange.

Fictional listing.''',
'''Does it use standard 72-pin SIMMs? Also, is the battery on a flying
lead or soldered to the board? Would like a photo of that area.''',
'''Hang on to the jumper sheet. Worth considerably more than a promise
that somebody remembers where the little black things went.''',
'''72-pin slots. Will check the battery arrangement before making claims.
Jumper sheet is in the bag with the board, a rare outbreak of organisation.

Fictional listing.'''])
