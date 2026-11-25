"""Six broadband ego contests (24 articles)."""
def extend(add):
    threads = [
('t3-arrival','general','posting from a T3. try to keep up.',('zer0_c00l','Mo Exchange','Doreen Mills'),[
'''sup dialup peasants.

currently posting over a T3. by the time your modem has finished its
little robot argument i have downloaded the page, read it, disagreed
with it and mirrored it to a second machine out of spite.

-- zer0_c00l
T3 | 128MB RAM | NO WINMODEMS | fear the throughput''',
'''Is this your connection, or the university lab again? Last week you
called the office printer "my networked PostScript rendering cluster".
It still had the accounts department's name on it.''',
'''Could you use some of this speed to answer the question about my cup
holder? The shop has now referred me to somebody called Optical Storage,
who sounds like he should work for an optician.''',
'''ownership is a bourgeois concept. i have physical access and a login.

also the lab closes at nine so if i stop replying it is scheduled
maintenance and absolutely not the caretaker making me leave.''']),
('cable-king','general','CABLE INSTALLED. modem now available as historical exhibit',('xX_CableGod_Xx','Ruth Handshake','Kev Baud'),[
'''it is done. cable is IN. no dialling. no screeching. no negotiating with
a relative who wants to telephone somebody for no technical reason.

opened six web pages at once just because i could. genuinely concerned
that i may finish the internet before the weekend.

56k users: please form an orderly queue to witness the future.''',
'''Glad it works. Please report sustained transfer rates, test duration
and the server used if you want a useful comparison. Opening six cached
pages is not a benchmark, however dramatic the account of it.''',
'''I opened seven windows on a 486 and heard the hard disk achieve
consciousness. Does that qualify me for cable or an exorcism?''',
'''benchmark currently suspended. installed a browser skin and now the
computer spends longer drawing the buttons than fetching the page.

this is obviously a graphics bottleneck. the connection remains elite.''']),
('ping-war','retro','7ms ping. seven. milliseconds.',('PacketLord','Bea IRQ','r00tbeer'),[
'''ping is 7ms. SEVEN. i have entered the realm where cause and effect
must submit a support ticket before proceeding.

if your ping has three digits you are not online, you are corresponding.
please do not reply until your packets have had a decent rest.''',
'''Seven milliseconds to what? Your gateway, a local machine or the game
server? A number without a destination is mostly decoration. Also,
latency and file-transfer capacity are different measurements.''',
'''mine is under 1ms to localhost. therefore my network is approximately
seven times more l33t. currently researching a negative-length cable.''',
'''it was the first router. game server is less cooperative.

withdrawing the causal supremacy claim pending negotiations with the
international telephone system. signature will remain unchanged.''']),
('screenshots-speed','general','re: your 900KB screenshot proving that you have cable',('Mara Packet','xX_CableGod_Xx','Doreen Mills'),[
'''Please stop attaching a full-screen bitmap to every explanation of how
fast your connection is. Some of us can read a transfer rate without a
photograph of the entire desktop.

You have included the taskbar, your wallpaper and a second window which
appears to contain a smaller version of the same screenshot.''',
'''the wallpaper is part of the benchmark environment. removing it would
invalidate the conditions under which the result was obtained.

also the bitmap arrives instantly here. cannot reproduce your issue.''',
'''I waited several minutes for the picture and all it proves is that you
have a blue background. My computer also has blue. Sometimes nothing
but blue. I do not make people download evidence of it.''',
'''Trim the picture or post the number as text. The point of having a
faster connection is not to make everybody else's connection slower.
Consider this a request to optimise the witness statements.''']),
('ftp-throne','help','FTP transfer too fast to read. UI bug?',('r00tbeer','Denise Carter','zer0_c00l'),[
'''on cable now. small downloads finish before i can admire the rate.
need a way to slow down the progress window WITHOUT slowing the network.

before anybody suggests a modem: i am asking a technical question, not
requesting a lifestyle downgrade.''',
'''Use a larger suitable test file and a timer, or read the completed
transfer statistics. Do not keep downloading the same file just to make
the counter look impressive. Check whether a cache affects the result.''',
'''on the T3 i have this problem with entire operating systems.

not authorised to install them on the lab machines, admittedly, but
that is a political restriction rather than a bandwidth limitation.''',
'''found completed-transfer statistics. devastating development: now i
must read numbers instead of watching the little bars.

still faster than you. have not established who "you" is. broadly you.''']),
('home-t3','general','WTB: domestic T3, must work with existing telephone extension',('PacketLord','Mo Exchange','Stan Rook'),[
'''planning T3 to the bedroom. requirements:
- no recurring bill that my father will notice
- existing telephone extension preferred
- should fit through the hole already drilled for the modem lead
- ideally supplier accepts a sound card in part exchange

please, serious technical answers only. i know what i am doing.''',
'''That is a leased telecom service, not a modem upgrade you plug into
the existing phone extension. Get an actual availability and cost quote
before planning the hole. I suspect the bill condition ends the project.''',
'''Will this make the compressed files more dangerous? I have only just
reassured the family about the pressure in the ZIPs. If this line can
put forty Internet files into the cupboard simultaneously we need notice.''',
'''project temporarily reclassified as "using the college connection".

father has offered domestic access to the lawn mower. zero latency,
dedicated capacity, compulsory weekend service window. negotiations
remain hostile.''']),
]
    for key,group,subject,authors,bodies in threads:
        add(key,group,authors[0],subject,bodies[0])
        add(key+'-r1',group,authors[1],subject,bodies[1],key)
        add(key+'-r2',group,authors[2],subject,bodies[2],key)
        add(key+'-r3',group,authors[0],subject,bodies[3],key+'-r1')
