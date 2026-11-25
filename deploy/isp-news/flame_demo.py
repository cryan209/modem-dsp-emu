"""Three branching eighteen-article arguments, deliberately left unresolved."""
def extend(add):
    def war(key, group, rows):
        assert len(rows)==18
        keys=[]
        for index,(author,subject,body,parent) in enumerate(rows):
            ident=key+'-%02d'%index
            add(ident,group,author,subject,body,None if parent is None else keys[parent])
            keys.append(ident)

    war('milk-war','nz.general',[
('Rachel M','Communal milk: last person finishes it, last person replaces it', '''I do not think this is a revolutionary position. If you finish the
milk, buy the next bottle. Do not leave three drops in the bottom and
claim the bottle is still technically occupied.

One flatmate says this discriminates against the person who gets up
last. Another says he only uses milk in tea and therefore owes a
fraction of the replacement cost. We are talking about milk, not a
hydroelectric development.

Can somebody who has successfully lived with other people explain how
they handled this WITHOUT buying five separately labelled bottles?''',None),
('June Ledger','Communal milk: last person finishes it, last person replaces it', '''The rule punishes whoever happens to pour the last cup, not whoever
used most of the bottle. Put a fixed contribution in a household kitty.
This is not difficult unless you insist on turning breakfast into a
moral examination.''',0),
('Graham S','Communal milk: last person finishes it, last person replaces it', '''Separate bottles. You have rejected the solution in the question.

Next.''',0),
('Rachel M','Communal milk: last person finishes it, last person replaces it', '''> You have rejected the solution in the question.

Because the fridge is small, Graham. That was the reason for asking
about another arrangement. I thought the word WITHOUT covered that.''',2),
('Graham S','Fridge size (was: communal milk)', '''Then buy a larger fridge. Or live with fewer people.

Every time somebody asks a practical question here they exclude all
practical answers and complain when the remaining answers are bad.''',3),
('Colin R','Fridge size (was: communal milk)', '''That is quite a leap from a bottle of milk to moving house. Are you
an estate agent or simply being like this recreationally?''',4),
('June Ledger','Nobody has answered the household kitty suggestion', '''Three replies about buying appliances and nobody has addressed a
small regular contribution. Buy household staples from it. Keep the
receipts. Agree the amount. That is the entire proposal.

I ran a flat like this for two years. The roof remained on.''',1),
('Trevor Pike','Nobody has answered the household kitty suggestion', '''Who audits the kitty? If the kitty buys biscuits, are biscuits milk?
If the cat drinks milk, does it receive voting rights?''',6),
('June Ledger','Nobody has answered the household kitty suggestion', '''Thank you Trevor. I had wondered how long it would take.''',7),
('Dale Mercer','The principle matters', '''People who do not replace what they finish generally do not wash
the frying pan either. It is not the cost. It is the assumption that
somebody else will always deal with it.

The person saying "just buy a bigger fridge" is usually the person
who will not contribute to the bigger fridge.''',4),
('Graham S','The principle matters', '''> usually the person

You have never met me. You do not know what I contribute. Yet here
we have a complete psychological assessment based on refrigerator advice.

This group used to be capable of discussing a subject without deciding
that anybody who disagreed was morally defective.''',9),
('Mo Exchange','The principle matters', '''You suggested someone change their living arrangements because they
cannot fit five bottles in a fridge. You do not get to act bewildered
when the reply is less than appreciative.''',10),
('Helen Crate','Re: the cat', '''Cats should not be voting on household expenditure in the first
place. Ours would buy nothing except food and a different sofa.''',7),
('Rachel M','Communal milk: update', '''We tried the kitty tonight. One person has already asked whether
buying milk on special entitles him to keep the difference.

I would like to withdraw the sentence about this not being a
hydroelectric development.''',6),
('Stan Rook','Please trim your quotes', '''I have just downloaded Graham's entire message for the fourth time.
Some of us are paying to collect news. Delete the irrelevant quoted
parts rather than sending every previous opinion back to us with
"exactly" underneath.''',10),
('Graham S','Please trim your quotes', '''My last message quoted two words. Two. Perhaps you could read the
message before appointing yourself its editor.''',14),
('Stan Rook','Please trim your quotes', '''It was a general request. Not every message on the group is a
personal attack on your fridge.''',15),
('Nicky Retry','Wanted: any remaining milk', '''Shop is shut. Does anyone in this thread actually have milk?''',13),
])
    war('t3-war','general',[
('zer0_c00l','why are modem users still lecturing cable users?', '''every time someone mentions cable, the same three people appear to
explain that speed is not everything. yes. we know. it is still rather
a lot when you are downloading something.

have been online at the lab all afternoon. got more done than i used
to manage in a week of negotiating with a phone line. apparently this
is invalid because it is not my personal T3.

by that logic nobody can enjoy a bus unless they personally purchased
the bus. discuss. slowly, if necessary.''',None),
('Ruth Handshake','why are modem users still lecturing cable users?', '''Nobody said the faster connection was not useful. People asked you
to identify the connection you were measuring and stop describing a
shared institutional service as a domestic installation.

Those are different statements. You keep replying to the easier one.''',0),
('xX_CableGod_Xx','why are modem users still lecturing cable users?', '''this group hears "my download finished quickly" and responds with a
request for a laboratory report. some of you would ask for three
independent measurements before admitting a kettle had boiled.''',0),
('Denise Carter','Measurements (was: cable users)', '''If you claim a result proves something, explaining the measurement
is ordinary practice. If you merely want to say you enjoyed your new
connection, nobody needs to conduct a trial.''',2),
('PacketLord','Measurements (was: cable users)', '''my ping is excellent. do i need to lodge it with the council or can
i continue enjoying myself without planning consent?''',3),
('Bea IRQ','Measurements (was: cable users)', '''You measured the gateway last time. Are we doing that again?''',4),
('PacketLord','old accusations, zero new evidence', '''one early result was misunderstood and now it is apparently my
permanent biography. people make mistakes. i assume yours are kept in
a tasteful bound volume rather than constantly posted to the group.''',5),
('Kev Baud','old accusations, zero new evidence', '''A bound volume would exceed the attachment limit.''',6),
('Glen Buffer','Can we discuss availability instead?', '''Cable is not available at my place. A T3 is not remotely within my
budget. I have a modem and a connection that works most evenings.

Telling me I am a peasant is not helping either problem. Neither is
a lecture about how perfectly happy I ought to be with a slow line.
Some useful information between those two positions would be welcome.''',0),
('zer0_c00l','Can we discuss availability instead?', '''peasant was a joke. i do not literally believe you are working land
under a feudal telecommunications arrangement.''',8),
('Mo Exchange','Can we discuss availability instead?', '''Then perhaps stop using the joke when the people it is about keep
asking you to stop. You could also answer the question about what is
actually available in their area. That would be a novel direction.''',9),
('xX_CableGod_Xx','Can we discuss availability instead?', '''i cannot make the cable company lay cable. apparently i am now
responsible for both the network and everyone's emotional experience
of not being connected to it.''',10),
('Mara Packet','Nobody asked you to lay cable', '''Glen asked for useful information, not civil engineering. An account
of your install, the actual costs and what did not work would be useful.
Five posts explaining how elite you are are not that account.''',11),
('r00tbeer','Nobody asked you to lay cable', '''the install took longer than expected and the first speed test was
cached. there. two facts. i have injured my reputation for public service.''',12),
('Ruth Handshake','Nobody asked you to lay cable', '''That is genuinely useful context. Thank you.''',13),
('zer0_c00l','this entire discussion is missing the bus analogy', '''not ONE person has addressed the bus analogy. i thought it was a
rather strong analogy. much stronger than the last one about the toaster.''',1),
('Colin R','this entire discussion is missing the bus analogy', '''You can enjoy the bus. You cannot advertise it as your private car
and become offended when somebody asks why it has forty seats.''',15),
('zer0_c00l','this entire discussion is missing the bus analogy', '''lab closing. this is not a concession. will answer properly tomorrow.''',16),
])
    war('browser-war','retro',[
('Lance Frames','If your browser cannot show my page, upgrade your browser', '''Finished the new page. Frames, some moving text, proper coloured
headings. Tested here and it looks fine. Already received a complaint
from somebody using an older browser.

I cannot spend every weekend maintaining an archaeological collection
of browsers just so somebody can read my links without seeing a frame.
At some point people need to upgrade instead of asking the rest of us
to keep pretending the web is a typewriter.''',None),
('Jo Underconstruction','If your browser cannot show my page, upgrade your browser', '''Your links disappear completely in another browser. This is not a
complaint that the headings are the wrong colour. Put ordinary links
somewhere and the visitor can still use the site.''',0),
('Mark Ellis','If your browser cannot show my page, upgrade your browser', '''I am using an older machine. "Upgrade" is not one button which gives
me more memory and a new processor. I wanted a link from your page,
not a new hobby maintaining the software needed to read it.''',0),
('Lance Frames','If your browser cannot show my page, upgrade your browser', '''I did not promise support for every computer ever manufactured. If
it is too old to run the software, that is unfortunate, but it is not
a fault in my page.''',2),
('Bea IRQ','Fault versus choice', '''It is your choice to make the page inaccessible to some visitors.
It is their choice to say that the page is not useful to them.

You seem to want both the freedom to exclude them and an exemption
from hearing that they have been excluded.''',3),
('PacketLord','Fault versus choice', '''old browsers are holding the web back. there, somebody said it.''',4),
('Mo Exchange','Fault versus choice', '''Someone says it approximately every eleven minutes. It is among the
least endangered opinions on this server.''',5),
('Jo Underconstruction','The actual missing links', '''For clarity, I am not asking Lance to remove the frames. I am asking
for a short alternate list of the destination links. The text already
exists. This is a small addition, not a demand to redesign everything.''',1),
('Lance Frames','The actual missing links', '''Then people will use the plain list and not see the work I put into
the navigation. Which rather defeats the point of designing navigation.''',7),
('Rachel M','The actual missing links', '''I thought the point of navigation was finding things.''',8),
('Lance Frames','The actual missing links', '''Obviously. You know perfectly well what I meant.''',9),
('Denise Carter','The actual missing links', '''No, I am not sure we do. If the plain list gets visitors to the right
place more reliably, it has succeeded as navigation. The elaborate
version can still exist for visitors who want it.''',10),
('Kev Baud','Best viewed with a personal apology', '''Could the entry page detect the browser and insult the visitor in
an appropriate font? This would preserve both compatibility and the
current tone of the discussion.''',0),
('Graham S','Standards, for once', '''This is why authors should use documented standards rather than
whatever looked attractive in one browser. The same argument happened
with desktop publishing. Nobody learned anything then either.''',4),
('Lance Frames','Standards, for once', '''Your own page uses a background image that makes the text unreadable.
I looked. Before you write another sermon, perhaps attend to that.''',13),
('Graham S','My background is not the subject', '''It is readable on my machine. The question was your navigation.''',14),
('Jo Underconstruction','One list of links, please', '''I have typed the alternate list for you. It is shorter than this
message. You can put it below the frames notice and keep everything else.

I am going to make tea. I hope the web survives without my supervision.''',11),
('Nicky Retry','One list of links, please', '''Tried the page again. There is now a plain link saying CLICK HERE IF
YOU REFUSE TO UPGRADE.

It works. I suppose this counts as progress.''',16),
])
