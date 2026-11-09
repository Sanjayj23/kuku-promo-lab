"""Measure bounded search coverage and tie ordering without inference."""
import argparse
import collections
import json
from pathlib import Path
from kuku_promo_lab.ingest import read_pack
from kuku_promo_lab.parser import infer_speakers, parse
from kuku_promo_lab.projection import project
from kuku_promo_lab.schemas import Candidate
from kuku_promo_lab.search import provisional_score, shortlist, variants
from kuku_promo_lab.timeline import coordinates, included, register

def measure(input_path, run_dir):
    root=Path(run_dir)
    config=json.loads((root/'run_manifest.json').read_text())['config']
    files,_=read_pack(input_path,config)
    v2=config.get('editorial_version',1)==2
    speakers=infer_speakers(files) if v2 else None
    episodes={}
    for source in files:
        ep=parse(source,speakers,legacy=not v2)
        register(ep)
        episodes[ep.episode_id]=ep
    data=json.loads((root/'provisional.json').read_text())
    analyses={a['episode_id']:a['analysis'] for a in data['analyses']}
    bases=[]
    not_hookable=set()
    for raw in data['base_candidates']:
        base=Candidate(**raw)
        ep=episodes[base.episode_id]
        proposal=next(p for p in analyses[base.episode_id]['proposals']
                      if p['start_boundary_id']==base.start_boundary_id and p['end_boundary_id']==base.end_boundary_id)
        bases.append((base,proposal))
        hookable=False
        for cut in variants(base,ep,config)[0]:
            start,_=coordinates(cut,ep)
            hookable |= bool(project(cut,ep)[0]['opener_ids']) or any(
                b.kind=='action' and b.start_ms < start+3000 for b in included(cut,ep))
        if not hookable:
            not_hookable.add(base.family_id)
    selected,_=shortlist(bases,config)
    scores=collections.Counter(provisional_score(p) for _,p in bases)
    actual={json.loads(x)['family_id'] for x in (root/'candidate_families.jsonl').read_text().splitlines()}
    cutoff=provisional_score(selected[-1][1]) if selected else None
    return dict(bases=len(bases),bases_with_no_hookable_variant=len(not_hookable),
                reviewed_families=len(actual),reviewed_with_no_hookable_variant=len(actual & not_hookable),
                provisional_score_distribution=dict(sorted(scores.items())),
                selected_slots_in_score_tie_groups=sum(scores[provisional_score(p)]>1 for _,p in selected),
                cutoff_score=cutoff,cutoff_group_total=scores.get(cutoff,0),
                cutoff_group_selected=sum(provisional_score(p)==cutoff for _,p in selected),
                selection_slots_decided_by_family_id=(sum(provisional_score(p)==cutoff for _,p in selected)
                    if scores.get(cutoff,0)>sum(provisional_score(p)==cutoff for _,p in selected) else 0),
                tie_measure_definition='Selected entries sharing a provisional score use family_id for order; the cutoff group measures selection affected by that order.')

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--input',required=True);p.add_argument('--run',required=True);p.add_argument('--out',required=True)
    args=p.parse_args()
    result=measure(args.input,args.run)
    Path(args.out).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
