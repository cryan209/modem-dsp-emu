"""Generate 664 articles from 166 distinct conversation outlines."""
from pathlib import Path

OPENERS = ('Rachel M','Helen Crate','Colin R','Graham S','Fern Filename','Stan Rook','Andy North','Dale Mercer','June Ledger','Nicky Retry','Doreen Mills','Mark Ellis','Owen Bundle','Ash Jumper','Tessa Inbox')
HELPERS = ('Ruth Handshake','Denise Carter','Mara Packet','Mo Exchange','Bea IRQ','Jo Underconstruction')
INTERRUPTERS = ('Kev Baud','Trevor Pike','Pete Cassette','Lance Frames','r00tbeer','PacketLord')


def extend(add):
    group=None
    index=0
    for line in Path(__file__).with_name('thousand-threads.txt').read_text().splitlines():
        if line.startswith('['):
            group=line[1:-1]
            continue
        if not line:
            continue
        subject,opening,answer,interruption,followup=line.split('|')
        index+=1
        key='thousand-%03d'%index
        author=OPENERS[(index*7)%len(OPENERS)]
        helper=HELPERS[(index*5)%len(HELPERS)]
        other=INTERRUPTERS[index%len(INTERRUPTERS)]
        if group=='marketplace':
            opening+='\n\nFictional listing.'
        if group=='test' and subject=='Does a leading dot arrive intact?':
            opening+='\n\n.This line begins with a dot.'
        add(key,group,author,subject,opening)
        add(key+'-r1',group,helper,subject,answer,key)
        add(key+'-r2',group,other,subject,interruption,key)
        add(key+'-r3',group,author,subject,followup,key+'-r1')
    assert index==166,index
