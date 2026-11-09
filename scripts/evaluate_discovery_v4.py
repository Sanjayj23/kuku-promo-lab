"""Offline equal-cap discovery measurements; never claims semantic recall or new inference."""
import argparse
import json
from pathlib import Path

from kuku_promo_lab.analyst import legacy_base_windows
from kuku_promo_lab.discovery_v4 import complete_opener, deduplicate_events, menu, select_bases
from kuku_promo_lab.ingest import read_pack
from kuku_promo_lab.parser import infer_speakers, parse
from kuku_promo_lab.schemas import Candidate, config_from
from kuku_promo_lab.timeline import candidate, register, validate_cut, valid_windows


def measure(input_path, run_dir, config):
    files,_=read_pack(input_path,config);speakers=infer_speakers(files);episodes={}
    for source in files:
        ep=parse(source,speakers);register(ep);episodes[ep.episode_id]=ep
    rows=[]
    for eid,ep in sorted(episodes.items()):
        old=legacy_base_windows(ep,config);new,audit=menu(ep,config,old)
        old_feasible={p for p in old if complete_opener(ep.boundaries[p[0]]['ms'],ep)}
        if not old_feasible <= set(new):raise ValueError('V4 lost an original timing-feasible pair')
        for s,e in new:
            if validate_cut(candidate(ep,s,e,'measurement'),ep,config):raise ValueError('Invalid measured V4 cut')
        rows.append({'episode_id':eid,'all_valid_dialogue_pairs':len(list(valid_windows(ep,config,dialogue_only=True))),
            'old_pairs':len(old),'old_feasible_pairs':len(old_feasible),'v4_pairs':len(new),
            'old_feasible_starts':len({s for s,e in old_feasible}),'v4_feasible_starts':len({s for s,e in new}),
            'old_ends':len({e for s,e in old}),'v4_ends':len({e for s,e in new}),
            'unknown_visual_ledger':len(audit['unknown_openings']),'all_old_feasible_pairs_preserved':True})
    provisional=json.loads((Path(run_dir)/'provisional.json').read_text())
    proposals={a['episode_id']:a['analysis']['proposals'] for a in provisional['analyses']}
    bases=[]
    for raw in provisional['base_candidates']:
        c=Candidate(**raw)
        p=next(p for p in proposals[c.episode_id] if p['start_boundary_id']==c.start_boundary_id and p['end_boundary_id']==c.end_boundary_id)
        bases.append((c,p))
    kept,duplicates=deduplicate_events(bases,episodes)
    old,_,_=select_bases(kept,episodes,12);new,_,log=select_bases(kept,episodes,24)
    return {'inference_attempts':0,'episodes':rows,
        'totals':{k:sum(r[k] for r in rows) for k in rows[0] if k not in {'episode_id','all_old_feasible_pairs_preserved'}},
        'allocation_on_saved_analyst_proposals':{'raw':len(bases),'provisional_groups':len(kept),
            'duplicates':len(duplicates),'initial_12_scheduled':len(old),'initial_24_scheduled':len(new),
            'new_selection_log':log,'extra_reviews':'not performed offline'},
        'two_proposals_effect':'not measured without fresh analyst calls; synthetic contract coverage only',
        'boundary_optimization_advantage':'unverified; no editorial ratings or source-audited new outputs',
        'scope':'structural menu/allocation measurements; no claim about actual good promos'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--run',required=True)
    p.add_argument('--config',default='config/v4.json');p.add_argument('--out',required=True)
    a=p.parse_args();target=Path(a.out)
    if target.exists():raise ValueError('Measurement output already exists')
    result=measure(a.input,a.run,config_from(a.config));target.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['totals'],indent=2))
