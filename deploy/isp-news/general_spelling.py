"""Stable, selective rushed spelling for fictional general chatter only."""
import re

WORDS={'the':'teh','because':'becuase','really':'realy','people':'peopel','probably':'probly',
       'computer':'computor','receive':'recieve','received':'recieved','definitely':'definately',
       'separate':'seperate','their':'thier','something':'somthing','nothing':'nothign',
       'another':'anohter','about':'abotu','working':'workign','just':'jsut',
       "don't":'dont',"can't":'cant',"won't":'wont',"I'm":'im',"I've":'ive',"it's":'its'}
PATTERN=re.compile(r'https?://\S+|news://\S+|\b[A-Za-z]+(?:\x27[A-Za-z]+)?\b')


def apply(posts):
    changed=0
    for p in posts:
        if p.get('kind')=='devlog' or p['group'] not in ('xtra.general','nz.general'):
            continue
        # Whole in-group reference trees move together; no revised child points
        # at a retired old parent. Other groups retain their existing IDs.
        p['revision']=3
        seed=sum((i+1)*ord(c) for i,c in enumerate(p['key']))
        if seed%3:
            continue
        remaining=[3+seed%5]
        def typo(match):
            value=match.group()
            if value.startswith(('http://','https://','news://')):
                return value
            replacement=WORDS.get(value,WORDS.get(value.lower()))
            if replacement and remaining[0]:
                remaining[0]-=1
                if value.isupper():return replacement.upper()
                if value[0].isupper() and value not in ("I'm","I've"):return replacement.capitalize()
                return replacement
            return value
        before=p['body']
        p['body']='\n'.join(line if line.strip()=='Fictional listing.' else PATTERN.sub(typo,line) for line in p['body'].split('\n'))
        if seed%2==0:
            remaining[0]=2
            p['subject']=PATTERN.sub(typo,p['subject'])
        changed+=p['body']!=before
    return changed
