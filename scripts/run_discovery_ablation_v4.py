"""Small genuine discovery ablation: menu, then proposal count; no editorial claims."""
import argparse
import json
from pathlib import Path
import re

from kuku_promo_lab.analyst import analyze, base_candidates
from kuku_promo_lab.baselines import direct
from kuku_promo_lab.context import story_index
from kuku_promo_lab.discovery_v4 import deduplicate_events
from kuku_promo_lab.ingest import read_pack
from kuku_promo_lab.models import ModelClient
from kuku_promo_lab.parser import infer_speakers, parse
from kuku_promo_lab.schemas import config_from, plain
from kuku_promo_lab.timeline import register, validate_cut


def ablate(input_path, config, out):
    files,pack=read_pack(input_path,config)
    if not 1<=len(files)<=6:raise ValueError('Discovery pilot is limited to 1..6 development episodes')
    if config['provider']!='local' or not re.fullmatch(r'[a-f0-9]{64}',config['model_sha256']):
        raise ValueError('Require identified local model for genuine discovery calls')
    out=Path(out)
    if out.exists():raise ValueError('Ablation output exists')
    out.mkdir(parents=True)
    speakers=infer_speakers(files);episodes=[]
    for source in files:
        ep=parse(source,speakers);register(ep);episodes.append(ep)
    episodes.sort(key=lambda e:e.episode_id);index=story_index(episodes);ep_map={e.episode_id:e for e in episodes}
    total=0;arms=[]
    for name,policy,count in [('original_menu_one','original',1),('refill_menu_one','retain_feasible_refill',1),('refill_menu_two','retain_feasible_refill',2)]:
        folder=out/name
        cfg=dict(config,v4_menu_policy=policy,max_proposals_per_episode=count,
                 cache_dir=str(folder/'isolated-cache'),max_attempts=max(0,min(config['max_attempts'],36)-total))
        client=ModelClient(cfg,folder);analyses=[];failures=[];bases=[]
        for ep in episodes:
            try:
                result=analyze(ep,index,cfg,client);analyses.append({'episode_id':ep.episode_id,'analysis':result})
                bases.extend(base_candidates(ep,result))
            except (ValueError,RuntimeError,OSError) as exc:
                failures.append({'episode_id':ep.episode_id,'error':str(exc)})
        kept,duplicates=deduplicate_events(bases,ep_map)
        for c,_ in kept:
            if validate_cut(c,ep_map[c.episode_id],cfg):raise ValueError('Invalid ablation candidate')
        baseline,suppressed=direct(kept,ep_map,cfg)
        row={'arm':name,'menu':policy,'max_proposals_per_episode':count,
            'episodes_requested':len(episodes),'analyses':analyses,'raw_proposals':len(bases),
            'provisional_event_groups':len(kept),'duplicate_proposals':duplicates,
            'source_valid_groups':len(kept),'failures':failures,
            'inference':{'attempts':client.attempts,'cache_hits':client.cache_hits,'request_elapsed_s':client.latency_s,'usage':client.usage},
            'direct_baseline':baseline,'direct_suppressions':suppressed,
            'editorial_quality':'unreviewed; no automatic or human proof of superiority'}
        (folder/'results.json').write_text(json.dumps(row,indent=2,ensure_ascii=False)+'\n')
        total+=client.attempts;arms.append(row)
        (out/'ablation.json').write_text(json.dumps({'pack_hash':pack['pack_hash'],'arms':arms,
            'total_attempts':total,'separation':'A/B changes menu only; B/C changes count only; same V4 analyst prompt and model',
            'boundary_optimization':'not used in this discovery-only/direct baseline experiment',
            'human_ratings':'pending','true_unseen_story':'pending'},indent=2,ensure_ascii=False)+'\n')
    return {'total_attempts':total,'arms':[{'arm':r['arm'],'raw_proposals':r['raw_proposals'],'provisional_groups':r['provisional_event_groups'],'failures':len(r['failures'])} for r in arms]}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--config',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();print(json.dumps(ablate(a.input,config_from(a.config),a.out),indent=2))
