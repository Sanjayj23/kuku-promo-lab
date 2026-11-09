"""Independent ZIP-source coordinate check. Does not import application parsing."""
import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

RESERVED = {'CAMERA','ACTION','SOUND FX','SFX','LIGHTING','MUSIC','VFX','TRANSITION','TEXT','SUPER','TITLE','INSERT'}

def ms(value):
    parts=value.strip('() ').split(':')
    whole,_,fraction=parts[-1].partition('.')
    return ((int(parts[-3])*3600 if len(parts)==3 else 0)+int(parts[-2])*60+int(whole))*1000+int((fraction+'000')[:3])

def check(pack, run):
    run=Path(run)
    errors=[];sources={};blocks_checked=0;intervals=0
    def require(condition, code, identity):
        if not condition:errors.append({'code':code,'id':identity})
    with zipfile.ZipFile(pack) as archive:
        for ep in map(json.loads,(run/'episodes.jsonl').read_text().splitlines()):
            try:
                raw=archive.read(ep['source_name'])
                lines=raw.decode('utf-8-sig').splitlines()
                require(hashlib.sha256(raw).hexdigest()==ep['source_sha256'],'source_hash',ep['episode_id'])
                headers=[i for i,line in enumerate(lines) if re.fullmatch(r'\s*\*\*\s*.+?\s*:\s*\*\*\s*',line)]
                require(len(headers)==len(ep['blocks']),'block_count',ep['episode_id'])
                blocks={}
                for block in ep['blocks']:
                    bid=block['block_id'];span=lines[block['line_start']-1:block['line_end']]
                    require('\n'.join(span).strip()==block['raw_text'].strip(),'raw_line_span',bid)
                    match=next((re.fullmatch(r'\s*Start\s*:\s*\(?([\d:.]+)\)?\s*\|\s*End\s*:\s*\(?([\d:.]+)\)?\s*',line,re.I) for line in span if re.match(r'\s*Start\s*:',line,re.I)),None)
                    if match is None:raise ValueError('source timing missing')
                    start,end=ms(match[1]),ms(match[2])
                    require((start,end)==(block['start_ms'],block['end_ms']),'source_time',bid)
                    label=re.sub(r'\([^)]*\)','',span[0].strip().strip('*').rstrip(':')).strip().upper()
                    label=' '.join(label.split())
                    require(block['kind']!='dialogue' if label in RESERVED else block['kind'] in {'dialogue','ambiguous_tag'},'source_kind',bid)
                    index=next(i for i,line in enumerate(span) if re.match(r'\s*Start\s*:',line,re.I))
                    require('\n'.join(span[index+1:]).strip()==block['text'],'source_text',bid)
                    blocks[bid]=dict(start=start,end=end,kind=block['kind'],text=block['text'])
                    blocks_checked+=1
                ends=[ms(x) for x in re.findall(r'^\s*Scene\s+end\s*:\s*(.+)$','\n'.join(lines),re.M|re.I)]
                extent=max(ends) if ends else max(b['end'] for b in blocks.values())
                require(ep['extent_ms']==extent,'extent',ep['episode_id'])
                sources[ep['episode_id']]=dict(blocks=blocks,extent=extent)
            except (ValueError,KeyError,IndexError,StopIteration) as exc:
                errors.append({'code':'source_read_error','id':ep['episode_id'],'type':type(exc).__name__})
    def interval(eid,start,end,identity):
        ep=sources[eid]
        require(0<=start<end<=ep['extent'] and 20000<=end-start<=60000,'interval_bounds',identity)
        edges={t for b in ep['blocks'].values() for t in (b['start'],b['end'])}
        require(start in edges and end in edges,'source_endpoint',identity)
        require(not any(b['start']<t<b['end'] for b in ep['blocks'].values() if b['kind']=='dialogue' for t in (start,end)),'speech_split',identity)
    records=json.loads((run/'provisional.json').read_text())['base_candidates']
    for name in ['baseline_direct.json','baseline_random.json']:
        records+=json.loads((run/name).read_text())['candidates']
    records += [r for f in map(json.loads,(run/'candidate_families.jsonl').read_text().splitlines()) for r in f['variants']]
    for record in records:
        cut=record.get('candidate',record);cid=cut['candidate_id']
        try:
            start,end=[int(cut[k].split(':t')[1]) for k in ('start_boundary_id','end_boundary_id')]
            interval(cut['episode_id'],start,end,cid)
            if 'start_ms' in record:
                require((start,end,end-start)==(record['start_ms'],record['end_ms'],record['duration_ms']),'saved_coordinate',cid)
            for u in record.get('packet',{}).get('utterances',[]):
                b=sources[cut['episode_id']]['blocks'][record['mapping'][u['id']]]
                require(u['text']==b['text'] and (u['start_ms'],u['end_ms'])==(b['start']-start,b['end']-start),'projection',cid)
            intervals+=1
        except (KeyError,ValueError,IndexError) as exc:errors.append({'code':'interval_read_error','id':cid,'type':type(exc).__name__})
    promos=json.loads((run/'promos.json').read_text())['recommendations']
    for promo in promos:
        try:
            seg=promo['segments'][0]
            interval(seg['source_episode'],seg['start_ms'],seg['end_ms'],promo['promo_id'])
            for bid in promo['hook']['evidence_ids']:
                b=sources[seg['source_episode']]['blocks'][bid]
                require(b['kind']=='dialogue' and seg['start_ms']<=b['start']<b['end']<=seg['start_ms']+3000,'hook_timing',bid)
        except (KeyError,ValueError,IndexError) as exc:errors.append({'code':'export_read_error','id':promo['promo_id'],'type':type(exc).__name__})
    return dict(episodes=len(sources),raw_blocks_checked=blocks_checked,artifact_interval_records_checked=intervals,final_intervals_checked=len(promos),errors=errors,valid=not errors)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--input',required=True);p.add_argument('--run',required=True);p.add_argument('--out',required=True)
    args=p.parse_args()
    result=check(args.input,args.run)
    Path(args.out).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if result['valid'] else 2)
