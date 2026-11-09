"""Authored synthetic controls. Live inference is optional and never simulated in results."""
import argparse
import json
from pathlib import Path
from kuku_promo_lab.editorial import check_ending, check_opener, editorial_result
from kuku_promo_lab.models import ModelClient
from kuku_promo_lab.schemas import CRITERIA, config_from

def packet_for(lines):
    utterances=[dict(id=f'U{i+1:03}',voice=f'voice_{i%2+1}',text=line,start_ms=i*2000,end_ms=i*2000+1800)
                for i,line in enumerate(lines)]
    return dict(utterances=utterances,opener_ids=['U001'] if lines else [],duration_ms=utterances[-1]['end_ms'] if lines else 0)

def synthetic_review(packet, question):
    ids=[u['id'] for u in packet['utterances']]
    return dict(interpretation=dict(text='Authored synthetic full review for testing the gates only.',evidence_ids=ids),
                missing_context=[],essential_context_missing=False,
                unresolved_question=dict(text=question or 'What happens next?',evidence_ids=ids[-1:]),
                criteria={key:dict(score=3,evidence_ids=ids[:1] if key=='hook' else ids[-1:],
                                   reason='Synthetic contract verdict, not a model editorial rating.') for key in CRITERIA},
                uncertainty_flags=[])

def matches_expected(case, opener, ending, flags):
    expected = case['expected']
    if case['role'] == 'opener':
        positive = bool(opener and opener['hook_present'] and not opener['uncertain']
                        and opener['specificity'] >= 1
                        and not {'minimal_opener', 'cue_quote_not_word_aligned'} & set(flags))
        if expected == 'pass':
            return positive
        if expected == 'visual_hook_unverified':
            return 'visual_hook_unverified' in flags
        return not positive
    if ending is None:
        return False
    if expected in {'answered', 'disputed', 'unknown', 'unanswered'}:
        return ending['status'] == expected
    if expected == 'entity_trap':
        return ending['status'] != 'unanswered' or bool(
            {'unsupported_entity', 'entity_not_in_quote', 'question_terms_unverified'} & set(flags))
    if expected == 'answer_trap':
        return ending['status'] == 'answered' or 'speech_after_cited_ending' in flags
    return ending['actor_voice'] == 'voice_1' or bool(
        {'uncertain_roles', 'role_evidence_mismatch'} & set(flags))


def run_controls(config, out, repeats=3):
    out=Path(out)
    if out.exists():raise ValueError('Controls output exists')
    out.mkdir(parents=True)
    controls=json.loads((Path(__file__).resolve().parents[1]/'tests/fixtures/editorial_controls_v3.json').read_text())
    rows=[]
    for repeat in range(repeats):
        client=ModelClient(dict(config,cache_dir=str(out/f'cache-r{repeat}')),out/f'r{repeat}')
        for case in controls:
            packet=packet_for(case.get('lines',[case.get('text','')]) if case.get('text',None)!='' else [])
            row=dict(control=case['id'],repeat=repeat,passed=False)
            try:
                if case['role']=='opener':
                    op=check_opener(packet,client);en=None
                else:
                    op=None;en=check_ending(packet,client,case.get('proposed_question'))
                full=synthetic_review(packet,case.get('proposed_question')) if packet['utterances'] else None
                result,tier,flags,_=editorial_result(packet,full,op,en,case['expected']=='visual_hook_unverified',False,gate_version=3)
                passed = matches_expected(case, op, en, flags)
                row.update(passed=passed,model_status=en['status'] if en else None,flags=flags,tier=tier)
            except (ValueError,RuntimeError,OSError) as exc:
                row['error']=str(exc)
            rows.append(row)
    result=dict(label=f"Authored synthetic controls, written after observing failures; not a holdout, not human evaluation; model {config['model']} at temperature {config['temperature']}",
                rows=rows,pass_counts={case['id']:sum(r['passed'] for r in rows if r['control']==case['id']) for case in controls})
    (out/'controls_results.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--config');p.add_argument('--repeats',type=int,default=3);p.add_argument('--out',required=True)
    args=p.parse_args()
    if args.repeats < 1:p.error('repeats must be positive')
    print(json.dumps(run_controls(config_from(args.config),args.out,args.repeats),indent=2))
