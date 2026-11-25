"""Eight mid-1990s New Zealand everyday-life threads (32 articles)."""
def extend(add):
    threads=[
('nz-flat-fridge','Who keeps putting an empty milk bottle back in the fridge?',('Rachel M','Pete Cassette','Doreen Mills'),[
'''Flat meeting tonight. Somebody has developed the ability to leave
exactly one teaspoon of milk in the bottle, thereby technically not
finishing it and avoiding the trip to the dairy.

We have five adults, three rolls of masking tape with names on them and
no milk. Is this normal flatting or an advanced economics experiment?''',
'''Our flat tried individual milk. Required more shelf space than the
fridge possessed. We now have communal milk and individual accusations.''',
'''Put a pencil mark beside the level before bed. This is how we tracked
down the person consuming the cooking sherry. Turned out to be the cook,
which complicated disciplinary proceedings.''',
'''Meeting result: we are buying a second bottle and rotating the dairy
run. The person responsible arrived with a carton of juice and has been
appointed secretary because he clearly understands loopholes.''']),
('nz-tolls','Telecom bill has arrived. Nobody admits knowing Wellington.',('Helen Crate','June Ledger','Trevor Pike'),[
'''There are toll calls to Wellington on the flat phone bill. Everybody
claims to know nobody there. Apparently the capital is populated entirely
by people unrelated to us.

I am not dividing this equally until somebody recognises a number.''',
'''Keep a notebook by the phone for toll calls and agree how shared costs
are divided. Work out the existing mystery before creating a second
mystery about the arithmetic.''',
'''Ask whether anyone has been ringing a radio competition. A flatmate
once maintained that a chance to win a toaster constituted investment.''',
'''Found it. Someone was calling about a job and did not think interviews
counted because he was not enjoying the conversation. Notebook installed.
No ruling yet on whether unemployment excuses the stationery costs.''']),
('nz-video-shop','Video shop: all new releases out, every action sequel available',('Rachel M','Stan Rook','Mark Ellis'),[
'''Went down after tea to get a video. Everything we agreed on was out.
Spent forty minutes discussing a film neither of us wanted, then hired
something because it was starting to rain outside.

Surely there is a better system than reading the backs of boxes until
one person becomes too tired to object?''',
'''Choose two titles before leaving home. Then spend forty minutes arguing
about which unwanted third title most closely resembles either of them.
It is a social occasion, not a distribution problem.''',
'''Our flat has a veto rule. Each person may reject one film. Unfortunately
it has taught us that we dislike more films than there are people.''',
'''Watched the consolation rental. Best bit was the trailer. Now trying to
find that film, which the shop has only on a box that says COMING SOON.
We are trapped inside a promotional cycle.''']),
('nz-rugby-tv','So which household are we watching the rugby at now?',('Graham S','Mo Exchange','Trevor Pike'),[
'''Trying to organise the weekend's rugby. Some matches appear to require
knowing somebody with Sky, which has turned my social life into a survey
of other people's television subscriptions.

If we are all going to one flat, who brings the chips and who sits on
the chair with the broken spring?''',
'''Confirm who actually has the match before buying supplies. Last time
we arrived with enough food for a small tour party and watched a replay
of something our host had already told us the result of.''',
'''Broken-spring chair goes to the person who talks through the commentary.
It is important that penalties apply consistently at domestic level.''',
'''Venue found. Host says shoes off and no arguments with the television.
Nobody agreed to the second condition, but we all nodded to preserve
access to the lounge.''']),
('nz-holiday-car','Auckland to Wellington: departure time now a constitutional issue',('Colin R','Rachel M','Doreen Mills'),[
'''Driving down for the weekend. I say leave before dawn. Passenger says
nothing sensible happens before breakfast. Another wants to stop at
every place selling a large meat pie.

At present we expect to reach Hamilton sometime after an argument.''',
'''Agree the first stop before you leave, and take turns driving if both
are rested and able. A schedule that depends on nobody needing a break
will not survive the first thermos.''',
'''We always stop when the driver gets cross. Sometimes this means buying
a pie before we have left our own suburb, but road safety is important.''',
'''Leaving early with breakfast packed. The pie enthusiast has produced
a list of stops on a folded road map. It looks less like a trip and
more like a regional inspection of pastry standards.''']),
('nz-supermarket','Supermarket queue: why is the fastest checkout never mine?',('June Ledger','Stan Rook','Trevor Pike'),[
'''Changed queues twice and watched both abandoned queues move faster.
The person ahead then produced coupons, a question about potatoes and
a cheque book. I had a loaf of bread and hopes for the afternoon.

Is there an accepted method of choosing, or does the building know?''',
'''Stay in the first queue. It will not be quicker, but it reduces the
number of separate mistakes available for your family to discuss.''',
'''Choose the one behind the man carrying only batteries. He has either
a very simple purchase or an argument about which packet was on special.
There is no middle ground.''',
'''Next time I am going to the dairy. It may cost more, but I can finish
the transaction before becoming personally invested in a stranger's
choice of tinned fruit.''']),
('nz-flats-ad','Flatmate wanted: must understand that the lounge is not a shed',('Rachel M','Bea IRQ','Kev Baud'),[
'''Room in an established flat. Looking for someone who pays their share,
cleans the bathroom occasionally and does not rebuild a motorcycle
between the sofa and the television.

Last requirement has regrettably needed to be stated. Shared phone,
no smoking inside, references appreciated.

Fictional listing.''',
'''Define "rebuild". Is a carburettor on newspaper acceptable if it is
only there overnight? Asking because your last flatmate may be applying
under a different spelling of his first name.''',
'''Does the room have enough sockets for two computers? I can promise no
motorcycles, but the power arrangements may attract the same objections.''',
'''No engine parts in the lounge. No extension leads across the hall.
Applicant who asked whether visitors may "occasionally" sleep in the
bath has not progressed to the next stage.''']),
('nz-tv-remote','Television remote has disappeared again. Household paralysed.',('Doreen Mills','Mark Ellis','Graham S'),[
'''Nobody can find the remote. The television is showing the wrong channel
and four perfectly healthy people are sitting beside it discussing the
problem as though we are snowed in.

I have suggested the buttons on the front. This was not well received.''',
'''Look under the Listener, inside the sofa and beside whoever last said
"I am not watching this". Those are the usual three storage locations.''',
'''Our remote lives on top of the television, which does defeat part of
the original concept. Still saves walking back after pressing a button,
provided you do not think about it too long.''',
'''Found in the kitchen beside the cordless phone. My husband has spent
the evening trying to turn up the news with the telephone. He says the
buttons are too similar. The telephone has significantly more numbers.''']),
]
    for key,subject,authors,bodies in threads:
        add(key,'nz.general',authors[0],subject,bodies[0])
        add(key+'-r1','nz.general',authors[1],subject,bodies[1],key)
        add(key+'-r2','nz.general',authors[2],subject,bodies[2],key)
        add(key+'-r3','nz.general',authors[0],subject,bodies[3],key+'-r1')
