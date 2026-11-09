from pathlib import Path
import json
from kuku_promo_lab.schemas import obj, STR, STRINGS, IDS, SCORE, CLAIM, CRITERIA
positive={'type':'integer','minimum':1}
ms={'type':'integer','minimum':0}
sha={'type':'string','pattern':'^[a-f0-9]{64}$'}
tc={'type':'string','pattern':r'^\d{2}:\d{2}:\d{2}\.\d{3}$'}
criterion=obj({'score':SCORE,'evidence_ids':IDS,'reason':STR})
segment=obj({'order':{'const':1},'source_episode':STR,'source_sha256':sha,'start_boundary_id':STR,'end_boundary_id':STR,'start_ms':ms,'end_ms':ms,'duration_ms':positive,'start_timecode':tc,'end_timecode':tc})
source=obj({'evidence_id':STR,'source_episode':STR,'source_sha256':sha,'block_id':STR,'line_start':positive,'line_end':positive})
episode=obj({'episode_id':{'type':['string','null']},'source_name':STR,'source_sha256':sha,'parse_status':{'enum':['parsed','failed']},'analysis_status':{'enum':['analyzed','no_candidate','failed','not_requested']},'error':{'type':['string','null']}})
episode['properties']['error_type'] = STR
coverage=obj({'files_discovered':ms,'episodes_parsed':ms,'episodes_analyzed':ms,'episodes_failed':ms,'episodes':{'type':'array','items':episode}})
promo=obj({'promo_id':STR,'rank':positive,'family_id':STR,'segments':{'type':'array','minItems':1,'maxItems':1,'items':segment},'duration_ms':{'type':'integer','minimum':20000,'maximum':60000},'hook':CLAIM,'cliffhanger':CLAIM,'why_it_works':dict(STR,maxLength=400),'source_evidence':{'type':'array','minItems':1,'items':source},'ranking':obj({'score':{'type':'number','minimum':0,'maximum':100},'formula_version':{'const':'editorial-v1'},'rationale':STR}),'quality':obj({'criteria':obj({k:criterion for k in CRITERIA}),'redundancy_score':SCORE,'structural_valid':{'const':True},'evidence_resolved':{'const':True},'semantic_status':{'const':'model_checked'},'review_basis':{'const':'dialogue_only'},'timing_basis':{'const':'script'},'video_verified':{'const':False},'human_status':{'const':'not_reviewed'},'flags':STRINGS})})
promo['properties'].update({
    'hook_quote': STR, 'ending_quote': STR, 'question_status': {'const':'unanswered'},
    'tier': {'const':'A'}, 'tie_group': STR, 'source_validation_status': {'const':'passed'},
    'human_review_status': {'const':'pending'}, 'human_posting_order': {'type':'null'},
})
promo['properties']['boundary_decision'] = obj({
    'winner_variant': STR, 'winner_interval_ms': {'type':'array','items':ms,'minItems':2,'maxItems':2},
    'base_interval_ms': {'type':['array','null'],'items':ms,'minItems':2,'maxItems':2},
    'alternatives': {'type':'array','items':obj({
        'candidate_id': STR, 'variant': STR, 'interval_ms': {'type':'array','items':ms,'minItems':2,'maxItems':2},
        'tier': {'type':['string','null']}, 'decided_by': {'enum':['tier','weighted_score','comprehension','cliffhanger','hook','shorter_duration','candidate_id']},
        'flags_absent_on_winner': STRINGS})},
    'unreviewed_alternatives': ms, 'tie_break_only': {'type':'boolean'}, 'summary':dict(STR,maxLength=300)})
from kuku_promo_lab.editorial_v4 import EVIDENCE_MODEL
resolution = obj({'flag': STR, 'basis': STR, 'terms': STRINGS, 'entities': STRINGS}, required=['flag', 'basis'])
promo['properties']['semantic_evidence'] = obj({'contract_version': {'const': 4},
    'model': EVIDENCE_MODEL, 'legacy_flags': STRINGS,
    'resolved_flags': {'type': 'array', 'items': resolution},
    'entailment_status': {'const': 'model_judgment_not_human_verified'}})
schema=obj({'schema_version':{'const':'1.0'},'run_id':STR,'pack_hash':sha,'run_status':{'enum':['complete','partial','failed']},'coverage':coverage,'recommendations':{'type':'array','maxItems':10,'items':promo}})
schema['$schema']='https://json-schema.org/draft/2020-12/schema'
schema['title']='Kuku Promo Lab verified recommendation envelope'
text=json.dumps(schema,indent=2)+'\n'
for p in ['schemas/recommendation.schema.json','src/kuku_promo_lab/recommendation.schema.json']:
 Path(p).write_text(text)
