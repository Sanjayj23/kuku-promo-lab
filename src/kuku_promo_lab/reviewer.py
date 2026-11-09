from copy import deepcopy
from .schemas import CRITERIA, REVIEW_SCHEMA, STR, obj


def review_schema(packet):
    schema=deepcopy(REVIEW_SCHEMA)
    ids=[u['id'] for u in packet['utterances']]
    cited={'type':'array','items':{'enum':ids},'minItems':1,'maxItems':4,'uniqueItems':True}
    optional={'type':'array','items':{'enum':ids},'maxItems':4,'uniqueItems':True}
    reason=dict(STR,maxLength=200)
    for key in ['interpretation','unresolved_question']:
        schema['properties'][key]['properties']['evidence_ids']=cited
    for key in CRITERIA:
        options=[obj({'score':{'enum':[0,None]},'evidence_ids':optional,'reason':reason})]
        if key=='hook':
            options.append(obj({'score':{'const':1},'evidence_ids':cited,'reason':reason}))
            if packet['opener_ids']:
                options.append(obj({'score':{'enum':[2,3,4]},'evidence_ids':dict(cited,items={'enum':packet['opener_ids']}),'reason':reason}))
        else:
            options.append(obj({'score':{'enum':[1,2,3,4]},'evidence_ids':cited,'reason':reason}))
        schema['properties']['criteria']['properties'][key]={'oneOf':options}
    return schema


def validate_review(review, packet):
    ids = {u['id'] for u in packet['utterances']}
    for key in ['interpretation', 'unresolved_question']:
        if not set(review[key]['evidence_ids']) <= ids:
            raise ValueError(f'{key}: invented utterance ID')
    for key in CRITERIA:
        c = review['criteria'][key]
        if not set(c['evidence_ids']) <= ids:
            raise ValueError(f'{key}: invented utterance evidence')
        if c['score'] is not None and c['score'] > 0 and not c['evidence_ids']:
            raise ValueError(f'{key}: positive score requires evidence')
        if key == 'hook' and c['score'] is not None and c['score'] >= 2:
            if not c['evidence_ids'] or not set(c['evidence_ids']) <= set(packet['opener_ids']):
                raise ValueError('Hook >=2 requires a complete utterance inside the first three seconds')


def review(packet, client):
    if not packet['utterances']:
        raise ValueError('No dialogue: visual-only prospect requires human review')
    return client.generate('reviewer', packet, review_schema(packet), lambda r: validate_review(r, packet))
