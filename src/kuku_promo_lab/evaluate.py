import csv
import collections
import json
import random
from pathlib import Path

from .export import write_json
from .projection import project
from .ranking import score
from .schemas import Candidate, CRITERIA


def read_families(run_dir):
    return [json.loads(s) for s in (Path(run_dir)/'candidate_families.jsonl').read_text().splitlines()]


def equal_weight_key(promo):
    criteria = promo['quality']['criteria']
    # Change weights only; a different tie policy would confound the diagnostic.
    return (-sum(criteria[k]['score'] for k in CRITERIA),
            -criteria['comprehension']['score'], -criteria['cliffhanger']['score'],
            -criteria['hook']['score'], promo['duration_ms'], promo['promo_id'])


def demo_select(run_dir):
    families = read_families(run_dir)
    ranks={p['family_id']:p['rank'] for p in json.loads((Path(run_dir)/'promos.json').read_text())['recommendations']}
    choices = []
    for family in families:
        reviewed = [r for r in family['variants'] if r.get('review')]
        if len(reviewed) < 3 or not any(r['candidate']['variant'] == 'base' for r in reviewed):
            continue
        changed = len({bool(r['eligibility_errors']) for r in reviewed}) > 1 or any(
            len({r['review']['criteria'][k]['score'] for r in reviewed}) > 1 for k in ['comprehension', 'cliffhanger'])
        choices.append((not changed, ranks.get(family['family_id'],1000000), family['family_id'], family))
    return min(choices, key=lambda x: (x[0], x[1], x[2]))[3] if choices else None


def evaluate(run_dir, out, reference_v3=None):
    root, out = Path(run_dir), Path(out)
    if out.exists():
        raise ValueError('Evaluation output exists; choose a new directory')
    out.mkdir(parents=True)
    manifest = json.loads((root/'run_manifest.json').read_text())
    promos = json.loads((root/'promos.json').read_text())['recommendations']
    validation = json.loads((root/'validation.json').read_text())
    families = read_families(root)
    direct = json.loads((root/'baseline_direct.json').read_text())['candidates']
    random_rows = json.loads((root/'baseline_random.json').read_text())['candidates']
    call_logs=[json.loads(p.read_text()) for p in (root/'model_calls').glob('*.json')]
    analyst_logs=[r for r in call_logs if r['request']['role'] in {'analyst','v4/analyst'}]
    reviewer_logs=[r for r in call_logs if r['request']['role'] in {'reviewer','v4/reviewer'}]
    reviewed = [r for f in families for r in f['variants'] if r.get('review')]
    rejected = [r for r in reviewed if r['eligibility_errors']]
    weighted = [p['promo_id'] for p in promos]
    equal = sorted(promos, key=equal_weight_key)
    metrics = {'run_id': manifest['run_id'], 'coverage': {k: manifest['statistics'][k] for k in ['files_discovered','files_parsed','scenes','blocks','total_extent_ms']},
               'eligible_count': len(promos), 'final_valid': validation['valid'], 'valid_checked': validation['recommendations_checked'],
               'reviewed_variants': len(reviewed), 'editorially_rejected_variants': len(rejected),
               'editorial_rejection_reasons':dict(collections.Counter(e for r in rejected for e in r['eligibility_errors'])),
               'analyst_responses_recorded':len(analyst_logs),
               'invalid_analyst_responses':sum(not r['validated'] for r in analyst_logs),
               'invalid_analyst_response_rate':sum(not r['validated'] for r in analyst_logs)/len(analyst_logs) if analyst_logs else None,
               'invalid_reviewer_responses':sum(not r['validated'] for r in reviewer_logs),
               'raw_analyst_schema_violations':sum(not r.get('raw_schema_valid',r['validated']) for r in analyst_logs),
               'raw_reviewer_schema_violations':sum(not r.get('raw_schema_valid',r['validated']) for r in reviewer_logs),
               'losslessly_normalized_responses':sum(bool(r.get('normalizations')) for r in call_logs),
               'exact_duplicate_strings_removed':sum(n['removed_count'] for r in call_logs for n in r.get('normalizations',[])),
               'random_valid_count': len(random_rows), 'direct_selected_count': len(direct),
               'direct_scope_episodes': json.loads((root/'coverage.json').read_text())['episodes_analyzed'],
               'three_or_more_reviewed_variant_families': sum(sum(r.get('review') is not None for r in f['variants']) >= 3 for f in families),
               'winner_changed_from_base': sum(f['comparison'].get('winner_id') != f['comparison'].get('base_id') for f in families if f['comparison'].get('base_id')),
               'near_tie_families': sum(f['comparison'].get('near_tie',False) for f in families),
               'equal_weights_order': [p['promo_id'] for p in equal], 'weighted_order': weighted,
               'equal_weights_position_changes': sum(a != b for a,b in zip(weighted, [p['promo_id'] for p in equal])),
               'human_review': 'pending_no_ratings_collected', 'audience_or_conversion_advantage': 'not_tested',
               'internal_score_comparison_is_human_evidence': False,
               'runtime_s': manifest['elapsed_s'], 'inference': manifest['inference']}
    # Fixed-seed same-episode pairs; unavailable counterparts remain explicitly recorded.
    rng = random.Random(manifest['config']['seed'])
    pool = list(promos); rng.shuffle(pool)
    pairs, keys, unavailable = [], [], []
    episodes = {}
    from .schemas import Episode, Block, Scene
    for line in (root/'episodes.jsonl').read_text().splitlines():
        e = json.loads(line)
        e['blocks'] = [Block(**b) for b in e['blocks']]
        e['scenes'] = [Scene(**s) for s in e['scenes']]
        ep = Episode(**e); episodes[ep.episode_id] = ep
    for p in pool[:12]:
        s = p['segments'][0]
        match = next((d for d in direct if d['candidate']['episode_id'] == s['source_episode']), None)
        if match is None:
            unavailable.append({'promo_id': p['promo_id'], 'reason': 'no_same_episode_direct_top_k'})
            continue
        ep = episodes[s['source_episode']]
        chosen = Candidate(p['promo_id'], p['family_id'], ep.episode_id, s['start_boundary_id'], s['end_boundary_id'])
        direct_cut = Candidate(**match['candidate'])
        left, right = project(chosen, ep)[0], project(direct_cut, ep)[0]
        flip = rng.choice([False, True])
        pair_id = f'pair-{len(pairs)+1:02d}'
        pairs.append({'pair_id': pair_id, 'A': right if flip else left, 'B': left if flip else right,
                      'reviewer_1_first': 'A' if len(pairs)%2 == 0 else 'B', 'reviewer_2_first': 'B' if len(pairs)%2 == 0 else 'A'})
        keys.append({'pair_id': pair_id, 'proposed_side': 'B' if flip else 'A', 'proposed_id': chosen.candidate_id, 'direct_id': direct_cut.candidate_id})
    write_json(out/'blind_pairs.json', {'status': 'pending_authorized_reviewers', 'pairs': pairs, 'unavailable_pairs': unavailable})
    metrics.update(equal_weights_tie_breaks='same_as_primary', blind_pairs_available=len(pairs),
                   blind_pairs_unavailable=len(unavailable),
                   identical_blind_pairs=sum(p['A'] == p['B'] for p in pairs))
    if manifest['config'].get('editorial_gate_version') == 4:
        flags_by_group={f['family_id']:sorted({flag for r in f['variants'] for flag in r.get('flags',[])}) for f in families}
        metrics['v4']={
            'provisional_event_groups':manifest['statistics'].get('v4_discovery',{}).get('provisional_event_groups'),
            'groups_are_not_verified_narrative_events':True,
            'flags_by_group':dict(collections.Counter(flag for flags in flags_by_group.values() for flag in flags)),
            'native_ending_statuses_by_variant':dict(collections.Counter(
                r['semantic_ending']['status'] if r.get('semantic_ending') else 'unavailable' for f in families for r in f['variants'])),
            'lexical_resolution_records':sum(len(r['review'].get('grounding',{}).get('resolved_flags',[]))
                for f in families for r in f['variants'] if r.get('review')),
            'accepted_precision':None if not promos else 'requires independent source audit',
            'editorial_advantage_over_direct':'unverified_without_blind_human_ratings',
            'prospective_evaluation':'not established by this development command',
            'genuinely_unseen_story_evaluation':'pending separate authorized story',
            'v3_reference':None}
        if reference_v3:
            v3_root=Path(reference_v3)
            v3_manifest=json.loads((v3_root/'run_manifest.json').read_text())
            if v3_manifest['config'].get('editorial_gate_version')!=3:
                raise ValueError('Reference must be an actual gate-3 run')
            accepted=json.loads((v3_root/'promos.json').read_text())['recommendations']
            metrics['v4']['v3_reference']={'run_id':v3_manifest['run_id'],
                'actual_production_recommendations':len(accepted),
                'exploratory_B_cuts_counted_as_recommendations':False}
    write_json(out/'metrics.json', metrics)
    write_json(out/'blind_pair_key_PRIVATE.json', keys)
    fields = ['pair_id','reviewer_slot','first_exposure','opener_hook_evidence','cold_interpretation','preference_A_B_tie','comprehension_A','comprehension_B','cliffhanger_A','cliffhanger_B','boundary_A','boundary_B','reason','trim_needed']
    with (out/'human_ratings.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields); writer.writeheader()
        for pair in pairs:
            for slot in [1,2]:
                writer.writerow({'pair_id':pair['pair_id'],'reviewer_slot':slot,'first_exposure':pair[f'reviewer_{slot}_first']})
    with (out/'failure_analysis.csv').open('w',newline='') as f:
        csv.writer(f).writerow(['candidate_id','sample_group','symptom','root_stage','severity','source_evidence','correction','reviewer'])
    (out/'README.md').write_text('''# Evaluation protocol

Automatic metrics describe source validity and model decisions, not advertising effectiveness.
Direct baseline uses the same genuine story-aware analyst calls and rubric, without restricted review or neighbor search. Random baseline covers every parseable episode with seeded valid windows.

Human ratings are pending. Two people authorized to access the source should inspect opener-only packets first, then their counterbalanced first full clip and record its interpretation before seeing the alternative. A/B sides hide systems; keep the PRIVATE key away from raters. Collect choice/tie, rubric and reasons. Report pair-level counts and disagreements; do not call ratings independent audience trials.

Inspect top recommendations, rejected variants, and a fixed-seed sample of unshortlisted bases. Record actual failures in the blank failure worksheet. Automated flags are prompts for inspection, not human findings. Reading scripts is not watching video. No human votes have been generated.
''')
    write_json(out/'demo_family.json', demo_select(root))
    return {k:v for k,v in metrics.items() if k != 'inference'}
