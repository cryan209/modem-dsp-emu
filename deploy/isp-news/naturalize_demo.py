"""Keep the shared disclosure in the index, not in every discussion."""
import re

def clean(posts):
    for p in posts:
        p['subject']=p['subject'].replace('[demo] ', '')
        b=re.sub(r'(?im)^Fictional listing\.\s*$', '', p['body'])
        b=re.sub(r'(?im)^.*(?:DEMO LISTING|DEMO WANTED AD|DEMO SWAP|No real item|No actual exchange|No actual equipment|No payment details|No real delivery|Please do not send offers|Please do not make actual travel|No real offers or payments).*(?:\n|$)', '', b)
        b=re.sub(r'(?i)Fictional support ticket:|Imaginary mail question:|Imaginary web-space mystery:|Fictional support ticket:|Imaginary repair bench:|Fictional preservation question:|Invented etiquette question:|Fictional multilink ticket:|Fictional support ticket:|Fictional fault report:|Fictional bench tale:|Fictional workshop project:|Fictional workbench discovery:|Fictional community proposal:|Fictional test article for the web reader:', '', b)
        b=re.sub(r'(?i)in (?:this|the) (?:fictional|invented) (?:case|scenario|exercise|test)', 'in this case', b)
        b=re.sub(r'(?i)for this invented case', 'in this case', b)
        b=re.sub(r'(?i)\b(?:fictional|imaginary|invented|pretend|made-up|nonexistent|hypothetical)\s+', '', b)
        b=re.sub(r'(?i)\b(?:fictional|imaginary)\b', '', b)
        b=b.replace('The modem noise is real. The office politics below are not.', '')
        b=b.replace('No real offers or payments please.', '')
        b=b.replace('There is no public Usenet feed here.', 'There is no public Usenet feed here.')
        b=re.sub(r'(?im)^.*(?:scenery for the demo feed|scenery for the retro ISP|No actual equipment|not an announcement of a live|All seeded buy/sell|comic replies are not technical|No real purchases|No real sales|not offers to transact).*(?:\n|$)', '', b)
        b=re.sub(r' {2,}', ' ', b).strip()
        if p['group']=='xtra.marketplace' and not p['parent']:
            b += '\n\nFictional listing.'
        p['body']=b
    replacements={
      'free-magazines-r3':(None,'Reserved pending a reply. The cover discs are still being sorted; somebody has put a printer driver where the games should be.'),
      'wanted-keyboard-r1':(None,'I have one with a five-pin DIN plug. Check the connector and interface against your machine before buying an adapter.'),
      'wanted-keyboard-r3':(None,'Will check the manual first. The raisin is outside my budget.'),
      'welcome':('Welcome to the local newsgroups', 'Welcome aboard. General chat in xtra.general, practical questions in\nxtra.help, old hardware in xtra.retro. Try xtra.test if you are still\ngetting the hang of your reader.\n\nPlain text preferred. Trim your quotes and let us know when a fix works.\nAnnouncements are read only; discussion belongs in the other groups.'),
      'test-r1':(None,'No.'),
      'etiquette':(None,'A few requests:\n\nTrim repeated quotes and signatures. Reply in the thread. Give your\nmodem model and the actual error message when asking for help.\nDo not post passwords or other people\'s private mail.\n\nIf somebody\'s suggestion fixes it, say so. Nothing worse than finding\nyour exact problem in a thread which ends with "never mind, fixed it".'),
      'test':(None,'Can you see this in the web viewer and a newsreader?\n\nA reply is welcome. Trying to establish whether the absence of answers\nmeans a technical fault or that everyone is having tea.'),
      'sale-modem':(None,'External 33.6 modem, beige, matching power supply and serial lead.\n$20 ono. Upgraded and have been told there are too many boxes by the phone.\n\nFictional listing.'),
      'wanted-simms':(None,'Wanted: matched pair of 8 MB 72-pin SIMMs for a Pentium board.\nHave a manual and will check the requirements before buying. Could swap\nfor serial adapters, provided we can identify at least half of them.\n\nFictional listing.'),
      'free-printer':(None,'9-pin dot-matrix printer, free to a good home. Tractor feed, a little\ncontinuous stationery included. Ribbon prints, though not with enthusiasm.\nNoisy enough that the neighbours will know when your essay is finished.\n\nFictional listing.'),
      'sale-monitor':(None,'14-inch CRT, VGA lead included. Works, but I would like my desk back.\nOpen to swaps for something significantly smaller.\n\nFictional listing.'),
      'crt-purchase':(None,'15-inch CRT, beige, VGA lead included. Asking $35.\nWorks on the spare PC; selling because there is no longer room for both\nthe monitor and a keyboard on the desk.\n\nFictional listing.'),
      'wanted-keyboard':(None,'Wanted: AT keyboard with all the keys. Prefer less biscuit residue\nthan the one currently in service. Five-pin DIN connector.\n\nFictional listing.'),
      'free-magazines':(None,'Stack of late-90s computer magazines free to anybody with shelf space.\nSome cover discs included. Not promising that the disc mentioned on each\ncover is still in that particular magazine.\n\nFictional listing.'),
    }
    for p in posts:
        if p['key'] in replacements:
            subject,body=replacements[p['key']]
            if subject:p['subject']=subject
            p['body']=body
        if p['group']=='xtra.marketplace':
            p['subject']=re.sub(r'(?i)fictional |imaginary |in pretend currency', '', p['subject']).strip()
        if p['key']=='nz-flats-ad':
            p['body'] += '\n\nFictional listing.'
