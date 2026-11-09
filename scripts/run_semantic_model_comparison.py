"""Bounded genuine semantic tasks; reports authored expectations, never human ratings.

Run each model/policy separately with pinned runtime configs and independent caches.
This script never launches a server, downloads weights, or submits source remotely.
"""
import argparse
import json
from pathlib import Path
import re
import time

from kuku_promo_lab.editorial import check_ending as old_ending, check_opener as old_opener
from kuku_promo_lab.editorial_v4 import check_ending, check_full, check_opener
from kuku_promo_lab.models import ModelClient
from kuku_promo_lab.reviewer import review as old_review
from kuku_promo_lab.schemas import config_from


def synthetic_packet(case):
    lines = case['utterances'];step = 20000//max(1,len(lines)-1)
    utterances = [dict(id=f'U{i+1:03}', voice=voice, text=text, delivery=[],
        start_ms=i*step, end_ms=i*step+2400, complete_in_opener=i == 0,
        crosses_opener_unknown_prefix=False) for i,(voice,text) in enumerate(lines)]
    return {'rubric_version':'editorial-v1','duration_ms':22400,'hook_window_ms':3000,
            'utterances':utterances,'opener_ids':['U001']}


def run_comparison(cases, config, out, repeats=1, policy='v4'):
    if config['provider'] != 'local' or not re.fullmatch(r'[a-f0-9]{64}',config['model_sha256']):
        raise ValueError('Live comparison requires loopback local provider and exact checkpoint SHA256')
    if not 1 <= len(cases) <= 12 or not 1 <= repeats <= 2:
        raise ValueError('Pilot requires 1..12 cases and 1..2 independent repeats')
    out=Path(out)
    if out.exists():raise ValueError('Comparison output exists; choose an immutable new path')
    out.mkdir(parents=True)
    started=time.perf_counter();rows=[];totals={'attempts':0,'cache_hits':0,'request_elapsed_s':0,'logical_requests':0}
    expected_rows=[];client=None
    try:
        for repeat in range(repeats):
            remaining=config['max_attempts']-totals['attempts']
            client=ModelClient(dict(config,cache_dir=str(out/f'isolated-cache-r{repeat}'),max_attempts=max(0,remaining)),out/f'r{repeat}')
            for case in cases:
                row={'case_id':case['id'],'repeat':repeat,'case_origin':case['origin'],
                     'full_review':None,'opener':None,'ending':None,'errors':[]}
                packet=case['packet'];before=client.attempts
                for role in ['full_review','opener','ending']:
                    if client.attempts>=client.config['max_attempts']:
                        row['errors'].append({'role':role,'status':'not_run_attempt_budget'});continue
                    try:
                        totals['logical_requests']+=1
                        if role=='full_review':row[role]=(check_full if policy=='v4' else old_review)(packet,client)
                        elif role=='opener':row[role]=(check_opener if policy=='v4' else old_opener)(packet,client)
                        elif policy=='v4':row[role]=check_ending(packet,client,case['question'],case['proposition'])
                        else:row[role]=old_ending(packet,client,case['question'])
                    except (ValueError,RuntimeError,OSError) as exc:
                        row['errors'].append({'role':role,'error':str(exc)})
                row['attempts']=client.attempts-before
                rows.append(row)
                # Save progress before the next request. These are role diagnostics,
                # not complete gated recommendations; full review may ask another question.
                (out/'results.json').write_text(json.dumps(rows,indent=2,ensure_ascii=False)+'\n')
                expected=case.get('authored_expectations',{})
                end=row['ending'];op=row['opener']
                actor=(end['evidence_model']['actor']['voice'] if policy=='v4' else end['actor_voice']) if end else None
                expected_rows.append({'case_id':case['id'],'repeat':repeat,
                    'expected_status':expected.get('expected_status'),
                    'observed_status':end['status'] if end else None,
                    'status_matches_authored_expectation':bool(end and end['status'] in expected['expected_status']) if 'expected_status' in expected else None,
                    'actor_matches_authored_expectation':bool(end and actor==expected['expected_actor_voice']) if 'expected_actor_voice' in expected else None,
                    'hook_matches_authored_expectation':bool(op and op['hook_present']==expected['expected_hook']) if 'expected_hook' in expected else None,
                    'judgment_basis':'authored development expectations, NOT independent human gold'})
            totals['attempts']+=client.attempts;totals['cache_hits']+=client.cache_hits
            totals['request_elapsed_s']+=client.latency_s
            client=None
    finally:
        if client is not None:
            totals['attempts']+=client.attempts;totals['cache_hits']+=client.cache_hits;totals['request_elapsed_s']+=client.latency_s
        call_records=[json.loads(p.read_text()) for p in out.glob('r*/model_calls/*.json')]
        local_calls=[r for r in call_records if r.get('origin')=='local']
        observed_models=sorted({r['model'] for r in local_calls if r.get('validated')})
        usage={k:sum(r.get('raw_usage',{}).get(k,0) for r in local_calls) for k in ['prompt_tokens','completion_tokens']}
        report={'policy':policy,'model':{k:config[k] for k in ['model','model_sha256','runtime_identity','temperature','top_p','top_k_sampling','max_tokens']},
            'observed_validated_response_models':observed_models,
            'response_identity_matches_alias':all(m==config['model'] for m in observed_models) if observed_models else None,
            'reported_token_subtotal':usage,'calls_with_token_usage':sum('raw_usage' in r for r in local_calls),
            'case_count':len(cases),'distinct_case_ids':len({c['id'] for c in cases}),
            'repeats_are_not_independent_events':True,'repeats_requested':repeats,
            'completed_role_records':sum(r[k] is not None for r in rows for k in ['full_review','opener','ending']),
            'validation_or_transport_failures':sum(len(r['errors']) for r in rows),
            'inference':totals,'wall_elapsed_s':time.perf_counter()-started,
            'authored_expectation_comparisons':expected_rows,'independent_source_truth_audit':'pending',
            'human_editorial_ratings':'pending','recommendations':None,
            'scope':'role-level fixed-hypothesis diagnostics, not a gated traversal or editorial superiority result'}
        (out/'comparison.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True);parser.add_argument('--cases');parser.add_argument('--out',required=True)
    parser.add_argument('--policy',choices=['legacy','v4'],default='v4');parser.add_argument('--repeats',type=int,default=1)
    parser.add_argument('--case-ids',help='Comma-separated synthetic control IDs, at most 12')
    args=parser.parse_args()
    if args.cases:
        cases=json.loads(Path(args.cases).read_text())
    else:
        raw=json.loads((Path(__file__).parents[1]/'tests/fixtures/editorial_controls_v4.json').read_text())
        if args.case_ids:
            wanted=set(args.case_ids.split(','));raw=[c for c in raw if c['id'] in wanted]
            if {c['id'] for c in raw}!=wanted:raise ValueError('Unknown synthetic control ID')
        else:raw=raw[:12]
        cases=[{'id':c['id'],'origin':'authored_synthetic_development_control','packet':synthetic_packet(c),
                'question':c['question'],'proposition':c['proposition'],'authored_expectations':c} for c in raw]
    print(json.dumps(run_comparison(cases,config_from(args.config),args.out,args.repeats,args.policy),indent=2))

if __name__=='__main__':main()
