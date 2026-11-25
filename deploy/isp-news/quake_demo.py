"""Eight latency disputes, thirty-two articles."""
def extend(add):
    threads = [
('quake-tea','general','Quake freezes every time somebody wants a cup of tea',('Andy North','Ruth Handshake','Doreen Mills'),[
'''Saturday night Quake is fine until somebody in the house decides to
telephone a relative. Then my opponent teleports, I become a stain and
the modem hangs up. They say "it will only take a minute" as though the
other team has agreed to stop shooting during family administration.''',
'''If the call interrupts the same phone line, the game cannot keep the
connection alive by force of will. Agree on a time to use the line.
If you are also seeing trouble without calls, keep those tests separate.''',
'''The relative was checking whether we wanted a spare kettle. Some of us
are conducting useful business rather than arguing inside a brown castle.''',
'''I offered to telephone about the kettle after the match. Was informed
that the kettle might be gone by then. Apparently the used-appliance
market has lower latency requirements than competitive Quake.''']),
('quake-ping999','help','999 ping. I am watching my own death on a delay.',('r00tbeer','Mo Exchange','Kev Baud'),[
'''joined a Quake server and got 999. i fired a rocket, made a sandwich,
and returned in time to discover it had killed the person i used to be.

is the server particularly far away or is my modem sending the packets
individually with a covering letter?''',
'''What server, which connection and what else was transferring? Try a
nearer server with downloads stopped, and compare at another time.
A high game ping alone does not identify which part of the path is bad.''',
'''Please include sandwich type. If it was toasted, this is considerably
more serious than a basic cheese event.''',
'''nearby server much better. also discovered the browser was fetching a
large picture of a graphics card. apparently my pursuit of performance
was consuming the performance. sandwich was ordinary cheese.''']),
('doom-cable','retro','Doom still judders. Friend says cable would fix my 486.',('Ash Jumper','Bea IRQ','xX_CableGod_Xx'),[
'''Doom is choppy even when I am playing on my own. A mate says getting
cable will sort out the ping. I am not actually connected to anything.
Is this an especially bad case of lag?''',
'''If it happens in a local single-player game, start with the machine,
settings and workload. A faster Internet connection will not make local
rendering faster. Separate frame rate from network delay before spending
money on either a modem or a graphics upgrade.''',
'''cable improves morale, though. since installation i have blamed all
remaining problems on the processor with unprecedented confidence.''',
'''Reduced the display workload and it is smoother. Will save the cable
proposal for something it might actually improve. Friend now says the
real problem is the chair, so his diagnostic privileges are suspended.''']),
('quake-score','general','Ping compensation should include my score',('PacketLord','Mara Packet','Trevor Pike'),[
'''lost 18 to 2. opponent had lower ping. therefore my correct score is
at least 18, perhaps 24 if we account for international routing.

requesting a recognised formula that converts latency into the frags i
would obviously have achieved under laboratory conditions.''',
'''A connection disadvantage is real, but it does not tell you which shots
you would have landed. You could compare on the same server and record
the conditions. Awarding yourself imaginary kills is less reproducible.''',
'''We apply the same system to bowls. If it rains I am retrospectively
champion of the district. The club has refused to update the board.''',
'''reviewed the match. one death involved walking backwards into lava.
this may have been local prediction. the remaining seventeen are still
under independent investigation by me.''']),
('doom-two-cables','help','Doom between two PCs: shall I buy two Internet accounts?',('Dale Mercer','Denise Carter','zer0_c00l'),[
'''Two computers in the same room, wanting to play Doom. Brother suggests
we both dial out so the game can travel to the Internet and come back.
Seems a long journey when the machines are six feet apart.''',
'''Check what connection methods your exact game versions support. A
compatible local arrangement can avoid sending traffic out and back,
but the right cable, network setup or software depends on the versions.
List those before buying accounts or adapters.''',
'''on the T3 we solve this by having more computers than friends. network
configuration is technically flawless. social layer remains offline.''',
'''Will identify the versions first. Brother has accepted that distance
across the carpet and distance through the Internet are not equivalent.
He still insists his machine should host because it has a larger case.''']),
('quake-radio','general','Who is listening to radio during my match?',('xX_CableGod_Xx','DJ Bitbucket','Doreen Mills'),[
'''temporarily back on dialup. circumstances beyond my control. do NOT
update your address books.

Quake becomes dreadful when the radio is playing. this is unacceptable.
i should not have to choose between being elite and having a soundtrack.''',
'''The stream and game share the link. Stop the stream for a comparison;
choosing a low audio rate does not make its traffic disappear. You need
room for game packets rather than using the whole line continuously.''',
'''We had a soundtrack before the Internet. It was called the radio and
it did not stop the children from losing at their games. Highly efficient.''',
'''physical radio acquired. game improved. had forgotten that receiving
music through the air is a separate networking technology.

please refer to my present connection as legacy compatibility mode.''']),
('quake-lan-ping','retro','My ping is one. Why am I still losing?',('zer0_c00l','Bea IRQ','Kev Baud'),[
'''LAN game. ping approximately one. mouse freshly cleaned. keyboard
angled for optimum packet flow. lost repeatedly to somebody who calls
the monitor "the television bit".

requesting known faults in the concept of fairness.''',
'''At that point look at the game rather than the connection. Low latency
removes one obstacle; it does not provide map knowledge, timing or aim.
You could ask the other player what they are doing differently.''',
'''Have you tried sending your boasts in smaller packets? The present
volume may be exhausting the opponent's capacity to care.''',
'''asked. apparently they know where the armour respawns. i had spent
most of the evening explaining the hub.

will study the map. under protest. networking remains impeccable.''']),
('quake-night','general','Server is perfect at 2am. Can everyone else go to bed?',('Nicky Retry','Mo Exchange','Stan Rook'),[
'''Same server, same modem, miserable after dinner and lovely in the early
hours. Have considered a polite notice asking the entire country to
stop using the telephone network while I am playing Quake.''',
'''Keep timed comparisons and note packet loss as well as latency. Load
somewhere on the path could vary, but a time pattern does not locate the
bottleneck by itself. Compare another server before blaming one machine.''',
'''Happy to cooperate, but require written assurance that your late-night
rockets will not disturb the compressed files under our stairs.''',
'''Tried a second server and it behaves differently. Keeping notes now.
National bedtime proposal remains in draft because I cannot find the
email address for whoever is in charge of everybody.''']),
]
    assert len(threads)==8
    for key,group,subject,authors,bodies in threads:
        add(key,group,authors[0],subject,bodies[0])
        add(key+'-r1',group,authors[1],subject,bodies[1],key)
        add(key+'-r2',group,authors[2],subject,bodies[2],key)
        add(key+'-r3',group,authors[0],subject,bodies[3],key+'-r1')
