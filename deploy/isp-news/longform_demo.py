"""Longer originals with differently shaped follow-up discussions."""
def extend(add):
    def story(key,group,author,subject,body,replies):
        add(key,group,author,subject,body)
        keys=[key]
        for index,(name,title,text,parent) in enumerate(replies,1):
            ident=key+'-r'+str(index)
            add(ident,group,name,title,text,keys[parent])
            keys.append(ident)

    story('long-phone','nz.general','Rachel M','The flat telephone constitution, first draft, already disputed', '''
After last month's toll bill we have finally held the meeting everybody
has been avoiding. I wrote an agenda. This was my first mistake, because
we then spent fifteen minutes arguing whether a flat meeting was allowed
to have an agenda without becoming the sort of flat nobody wanted to
live in.

There are five of us. One works nights, one is looking for work, one has
a girlfriend in another town, one has discovered the Internet, and I have
apparently become the Department of Telecommunications because the bill
arrives in my name. It does not follow that I know why the phone company
charged for each call. It particularly does not follow that I can make
charges disappear by being sufficiently stern on the telephone.

Proposal one: a notebook beside the phone. You record toll calls. This
was rejected by the person who said it would be difficult to remember.
I suggested that remembering to pay for something you used might be a
useful adult skill. The meeting briefly moved onto the subject of my tone.
Proposal two: everyone pays an equal share. Rejected by the person with
no distant girlfriend, on the grounds that love should fund itself.
Proposal three: the person making the call pays for it. Accepted until
we reached the question of calls made for the benefit of the whole flat.

Apparently ordering pizza, ringing the landlord and asking somebody's
mother how to remove a stain are collective acts. Asking about a party
may be collective, depending on whether everyone is invited. Ringing a
job advertisement is an investment in future flat solvency, although
nobody has offered to share any resulting salary. We had not yet reached
the Internet when somebody announced that the oven was on.

So here is the draft: basic shared costs from the kitty; identifiable
personal toll calls paid by the caller; a note beside the phone; advance
agreement for long sessions on the line; necessary calls take priority,
with necessary defined by something other than the caller's enthusiasm.
No one is to unplug anybody else's connection as a negotiation tactic.

If your flat has a working system, I would like to hear it. If your
suggestion is that all five of us obtain separate houses and telephones,
please include the money with your reply. We have already considered
that option at considerable length and no practical depth.
''',[
('June Ledger','The flat telephone constitution, first draft, already disputed','''Put a small amount in the kitty for genuinely shared calls, and keep
the rest attributable. The crucial part is agreeing the rules before
the next bill, not retrospectively when everyone knows what they owe.

Also keep the bill somewhere accessible. One person becoming keeper of
the evidence tends to turn every disagreement into an interrogation.''',0),
('Graham S','Necessary calls are doing a lot of work here','''A girlfriend may consider her call necessary. A job seeker may consider
checking replies necessary. You have simply moved the argument into a
word you have not defined.''',0),
('Rachel M','Necessary calls are doing a lot of work here','''Yes, Graham, ordinary household agreements require people to exercise
judgement. I was not hoping to replace us with a panel of judges.

The phrase about a separate house was included with you particularly
in mind, so I am grateful you chose a different objection.''',2),
('Trevor Pike','Phone constitution: amendments','''Suggest a second notebook recording disputes about the first notebook.
A third could record disputes about who moved either notebook.''',1),
('Mo Exchange','Necessary calls are doing a lot of work here','''There is a useful difference between an arrangement that cannot answer
every conceivable case and no arrangement at all. Rachel is trying to
improve the actual household, not publish a mathematically complete theory
of girlfriends.''',2),
])
    story('long-cable','general','xX_CableGod_Xx','Cable installation diary: the part I omitted from the victory speech', '''
since several of you have suggested my previous report was light on
useful information, here is the less heroic version.

first, the desk had to move. the desk had been assembled in a position
from which it could apparently never leave. behind it were a dead pen,
three leads i had been blaming somebody else for losing, and enough dust
to require its own property assessment. the installer was polite about
this in the way people are polite when they are already writing a story
to tell their colleagues.

then we established that where i wanted the equipment and where it was
sensible to put the cable were not the same place. i had drawn a plan.
the plan did not include walls, access or the fact that the wardrobe was
fixed. i now understand why buildings are usually drawn before networks.
we agreed on a route which i described to the flat as temporary. nobody
believed this because they have seen my other temporary cables.

once it was working i fetched several familiar pages. they were much
quicker. i then timed one without considering whether the browser had
already stored it, posted an extremely confident number, and was invited
to learn what a cache was by six people simultaneously. i did know what
a cache was. i had simply hoped it would not become relevant in public.

proper comparisons are less cinematic. use the same actual transfer,
check that it really came over the connection, measure a useful interval,
and write down what else the machine and link were doing. tiny files
finish before the display tells a useful story. a remote server can be
the slow part. the computer can spend time doing things which are not
downloading. none of this makes the connection bad, but it does make my
original description of infinite speed difficult to defend.

what has actually changed is that using the connection is no longer a
household ceremony. i do not have to arrange an opening in the telephone
schedule to read one message. that is the benefit i notice most. i have
also spent a distressing amount of time reading arguments i previously
would have disconnected before finishing. progress has hidden costs.

this is not an apology for enjoying it. it is an apology for the bitmap.
i have looked at the size of the bitmap. even i can see the point now.
''',[
('Ruth Handshake','Cable installation diary: the part I omitted from the victory speech','''This is a much more useful account. The distinction between less effort
to get online and a bigger transfer number is worth making. Not every
benefit needs to be a claim that every part of the Internet is faster.''',0),
('Doreen Mills','What happened to the dust?','''You have carefully described the discovery of the dust but not who
cleaned it. My experience suggests the omission is significant.''',0),
])
    story('long-games','retro','Andy North','Games night post-mortem: seven computers, very little Quake', '''
I said I would write this up because the next person organising a games
night deserves a chance to make different mistakes.

We invited six people and assumed everybody knew what to bring. This
produced seven computers, five monitors, several bags described as all
the necessary cables, and one person who thought he was coming to play
on somebody else's machine. The person with the extra computer had no
keyboard for it. Naturally that was the machine he regarded as essential.
The first hour was spent borrowing parts from machines which then became
the next machines requiring borrowed parts.

The software was not as uniform as the invitations. People had different
versions, different settings and different ideas about what already
installed meant. In one case it meant a directory copied from a machine
no longer in the room. We should have checked the supported game versions
and a working setup in advance, not assumed six individual successes
would combine into a seventh and much larger success.

By the time we had a consistent arrangement, the discussion had become
competitive. The cable owner announced that he was accustomed to better
latency. The T3 owner established that he did not actually own a T3 but
had access to a computer near one. Somebody reported a very good ping to
a machine which was not participating. None of these contributions
identified the missing lead.

Eventually we played. One computer was visibly struggling locally, which
is not the same problem as network delay. One player complained about
lag immediately after walking into lava without another player present.
Two players knew the map, which turned out to be an unfair advantage
according to people who had spent the setup period explaining networking
instead of looking at the map. I include myself in that last category.

Next time: agree the versions and connection method; test an actual pair
of machines first; make a written equipment list; arrange safe power and
cable placement; choose a setup deadline after which we stop adding new
hardware; and have something enjoyable to do if it all fails. Also food.
Food must not depend on the machine currently being used as a print server.

We did have fun. We would have had more fun if we had not defined success
as proving that all seven machines could be made to work simultaneously.
The evening was rescued by people lending things and being patient, not
by the person repeatedly telling us that it worked at his place.
''',[
('Bea IRQ','Games night post-mortem: seven computers, very little Quake','''A known-good pair first is the important bit. Expand from a working
baseline. Otherwise every change has three possible explanations and
nobody remembers which combination last worked.''',0),
('zer0_c00l','Access to a computer near a T3','''the distance between the computer and the T3 is not relevant. i have
explained this several times. it is a connection, not a geographical
personality trait.''',0),
('Kev Baud','Access to a computer near a T3','''You arrived without a monitor. The distance to a working monitor
seemed relevant for most of the evening.''',2),
('Andy North','Games night post-mortem: seven computers, very little Quake','''The keyboard and monitor checklist will go out before the next invite.
I have also been asked not to describe putting pizza boxes on the spare
computer as load balancing.''',1),
])
    story('long-page','help','Jo Underconstruction','A page that works before the background arrives', '''
Several people asked what I meant by a small usable homepage, so here is
the practical version rather than another round of browser accusations.

Start with the reason somebody came. If the page is about your computer
collection, say what is in the collection and give links to the details.
If it contains a club's meeting time, put the time, place and contact
information in ordinary text. A visitor should not need to download a
picture of your heading to find out whether they have the right page.

Give links names which make sense without the surrounding layout.
"Photos of the restoration" tells me more than three separate links all
called "here". A link to a large file can say what it is and roughly how
large it is. That lets a dial-up visitor decide whether to fetch it now
rather than discovering its size after committing the household phone line.
Check those links after uploading, including their spelling and case.

Keep original pictures separately. Make a smaller copy for the page and
compare its actual size and appearance. A photograph does not need to be
as wide as your entire desktop to demonstrate that a computer exists.
A tiny flat-colour logo and a photograph may suit different formats.
Changing the display dimensions in HTML is not the same as reducing the
file you send. Test what happens when images are not available at all.

Then try the page in a narrow window and, if possible, another browser.
The objective is not to make every browser draw precisely the same
rectangle. It is to leave the contents and navigation usable when a
particular decoration is missing. Ordinary text links as an alternative
to elaborate navigation cost very little. They do not insult the work
you put into the elaborate version; they help somebody reach that work.

Finally, upload the actual files, visit the actual public address and
check it from there. Your local copy may forgive mistakes the server
does not. Keep a copy of the working page before experimenting. It is
much easier to discover what changed when you have something to compare.

You can still have a silly GIF. I am not organising a police force
against silly GIFs. I am merely suggesting that visitors should be able
to find the content while the silly GIF is arranging its entrance.
''',[
('Lance Frames','A page that works before the background arrives','''For the record, I have added the plain link list. People are using it.
I remain entitled to dislike how pleased everybody is about this.''',0),
])
    story('long-support','general','Mara Packet','How a simple support question became a biography of the modem', '''
Here is what happened in a recent conversation, with the identifying
details left out because I am not trying to restart the argument.
Somebody said a download was slow. Within three replies they had been
told to replace the modem, change the browser, change the phone company
and stop expecting miracles from an old computer. Nobody had asked how
slow it was, what was being downloaded or whether it happened every time.

The owner then supplied a complete history of the machine. That was
understandable, because everybody was confidently diagnosing a different
part of it. We acquired its purchase date, former operating system,
previous owner's opinion and the fact that somebody's nephew had once
called the modem a good one. We still did not have an elapsed time and
a byte count. The thread had become very informative about the family
and not particularly informative about the transfer.

A useful report can start much smaller. What did you try? What happened?
What exact message appeared? Does the problem repeat? What changed before
it began? If it is a speed question, measure a complete transfer and
identify what you measured. If it is a connection problem, distinguish
not connecting from connecting and then failing to deliver data. If the
screen is displaying an error, its words are more useful than the theory
that the computer has taken a dislike to you.

Replying also requires some restraint. Ask one useful question instead
of issuing six unrelated changes. Explain what a proposed test would
separate. Let the owner finish the test before declaring the result.
Keep a working setup or notes so a failed experiment can be undone.
When somebody reports that a suggestion did not help, that is evidence,
not an insult to the person who suggested it.

There is room for humour. Half this group runs on humour. But a joke
followed by an actual question is different from twelve people competing
to tell the owner that the machine is ancient. Some owners already know.
Some owners cannot replace it. Some faults would survive the replacement
because the proposed diagnosis was never tested in the first place.

The final answer in this particular case was a second transfer using
the connection in the background. No new modem was needed. We had spent
more time arguing about possible purchases than it took to establish
what else was running. Make of that what you will.
''',[
('Graham S','How a simple support question became a biography of the modem','''Sometimes an old machine really is the problem. Your account rather
conveniently ends in the one situation where a hardware recommendation
was not needed.''',0),
('Mara Packet','How a simple support question became a biography of the modem','''Yes. Then establish that it is the problem. I did not say hardware
never fails or becomes inadequate. I said a confident shopping list
before asking what happened is not diagnosis.''',1),
('Ruth Handshake','How a simple support question became a biography of the modem','''The point about what a test separates is important. If the answer to
"what will this tell us?" is merely "it might work", you are probably
changing too many things at once.''',0),
('Kev Baud','How a simple support question became a biography of the modem','''I have temporarily withdrawn my recommendation to reinstall the chair.
Evidence regarding its contribution remains inconclusive.''',3),
])
    story('long-wet-trip','nz.general','Colin R','Weekend away: a complete audit of what we forgot', '''
We have returned from the weekend at the bach. I am posting the inventory
because it may help another household avoid confusing packing thoroughly
with packing usefully.

We took three torches. Two had no batteries. The third had batteries of
uncertain age and produced enough light to confirm that the first two
were still not working. We also took a packet of batteries which fitted
none of the torches. This was described as bad luck rather than a planning
failure by the person who packed them. He had, however, remembered the
battery-powered radio, which provided excellent commentary on the weather
we could already see through the window.

There were four towels, all reserved by their owners for a different
purpose. Nobody would use a beach towel after a shower, or a shower towel
at the beach, or the good towel for anything that might make it wet.
The resulting negotiations took longer than the drive to the nearest
shop. We bought another towel. It has no history and is therefore the
most useful towel in the house.

The food planning assumed sunshine. The weather planning consisted of
hoping for sunshine. We had barbecue supplies, a very small frying pan
and several suggestions that the rain would stop shortly. Once it
became clear that shortly was not an actual cooking time, we organised
a perfectly good meal indoors. This required abandoning the original
meal plan, which was harder for some people than the original plan had
been to write on the back of an envelope.

Nobody remembered the address of the people who were going to meet us.
Everybody remembered that somebody else had it. The telephone number was
in a notebook at home because we had agreed not to carry too much stuff.
The notebook's absence was particularly impressive given the number of
other objects we had brought on the off chance that they might be useful.

Despite this, it was a good weekend. We walked, read, played cards and
had several conversations which were not about whether the modem was
occupying the telephone. Coming home took longer because we packed the
car in a way which prevented access to the road map, then pretended to
know the route. Next time I am making a list of things to check, rather
than simply a list of things to put in bags.

The new towel is staying with me. It has earned somebody competent.
''',[
('Doreen Mills','Weekend away: a complete audit of what we forgot','''A towel without a history soon develops one. Put it in the wrong wash
once and it becomes a gardening towel, which nobody may take to the bach.
You are enjoying a temporary administrative gap.''',0),
('Trevor Pike','The radio was the only prepared member of the party','''Three torches, wrong batteries and a working weather forecast. You did
not pack for a holiday. You packed for a committee inquiry into darkness.''',0),
])
