import json
from pathlib import Path

from .evidence import resolve_block_ids, source_ref
from .ingest import read_pack
from .models import check_schema
from .parser import infer_speakers, milliseconds, parse, timecode
from .ranking import eligibility, score, text_similarity
from .schemas import Candidate, CRITERIA
from .timeline import candidate, coordinates, included, register, validate_cut


def validate_envelope(envelope, episodes, config):
    schema = json.loads((Path(__file__).parent/'recommendation.schema.json').read_text())
    check_schema(envelope, schema)
    errors, checked, seen, families, prior_segments, prior_texts = [], 0, set(), set(), [], []
    ep_map = {e.episode_id: e for e in episodes}
    for rank, promo in enumerate(envelope['recommendations'], 1):
        pid = promo['promo_id']
        reasons = []
        if pid in seen or promo['family_id'] in families:
            reasons.append('duplicate_id_or_family')
        seen.add(pid); families.add(promo['family_id'])
        if promo['rank'] != rank:
            reasons.append('nonconsecutive_rank')
        seg = promo['segments'][0]
        episode = ep_map.get(seg['source_episode'])
        if episode is None:
            errors.append({'promo_id': pid, 'errors': ['unknown_episode']})
            continue
        cut = Candidate(pid, promo['family_id'], episode.episode_id, seg['start_boundary_id'], seg['end_boundary_id'])
        reasons += validate_cut(cut, episode, config)
        try:
            start, end = coordinates(cut, episode)
            if (seg['start_ms'], seg['end_ms'], seg['duration_ms'], promo['duration_ms']) != (start, end, end-start, end-start):
                reasons.append('numeric_timing_mismatch')
            if (seg['start_timecode'], seg['end_timecode']) != (timecode(start), timecode(end)):
                reasons.append('timecode_mismatch')
            if seg['source_sha256'] != episode.source_sha256:
                reasons.append('source_hash_mismatch')
            refs = {}
            for ref in promo['source_evidence']:
                if ref['evidence_id'] in refs:
                    reasons.append('duplicate_evidence')
                refs[ref['evidence_id']] = ref
                blocks = resolve_block_ids([ref['block_id']], cut, episode)
                if ref != source_ref(blocks[0], episode):
                    reasons.append('source_reference_mismatch')
            for key, claim in [('hook', promo['hook']), ('cliffhanger', promo['cliffhanger'])] + list(promo['quality']['criteria'].items()):
                if not set(claim['evidence_ids']) <= refs.keys():
                    reasons.append('unresolved_evidence')
                resolve_block_ids(claim['evidence_ids'], cut, episode, opener=key == 'hook', hook_ms=config['hook_window_ms'])
            criteria = promo['quality']['criteria']
            if promo['ranking']['score'] != score(criteria):
                reasons.append('score_mismatch')
            reasons += eligibility({'criteria': criteria, 'essential_context_missing': False})
            current_text='\n'.join(b.text for b in included(cut,episode,'dialogue'))
            max_similarity=0
            for other in prior_segments:
                if other['source_episode'] == seg['source_episode']:
                    overlap = max(0, min(other['end_ms'], end)-max(other['start_ms'], start))
                    union = max(other['end_ms'], end)-min(other['start_ms'], start)
                    if overlap/union >= .7:
                        reasons.append('duplicate_interval')
                    max_similarity=max(max_similarity,overlap/union)
            for text in prior_texts:
                similarity=text_similarity(current_text,text)
                if similarity >= .8:
                    reasons.append('duplicate_dialogue')
                max_similarity=max(max_similarity,similarity)
            redundancy=2 if max_similarity >= .3 else 3 if max_similarity > 0 else 4
            if promo['quality']['redundancy_score'] != redundancy:
                reasons.append('redundancy_mismatch')
            prior_segments.append(seg)
            prior_texts.append(current_text)
        except ValueError as exc:
            reasons.append(str(exc))
        checked += 1
        if reasons:
            errors.append({'promo_id': pid, 'errors': reasons})
    ordering=lambda p:(-p['ranking']['score'],-p['quality']['criteria']['comprehension']['score'],
                       -p['quality']['criteria']['cliffhanger']['score'],-p['quality']['criteria']['hook']['score'],p['duration_ms'],p['promo_id'])
    if envelope['recommendations'] != sorted(envelope['recommendations'],key=ordering):
        errors.append({'errors':['ranking_order_mismatch']})
    return {'valid': not errors, 'recommendations_checked': checked, 'errors': errors}


def validate_run(input_path, run_dir):
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir/'run_manifest.json').read_text())
    config = manifest['config']
    v4 = config.get('editorial_gate_version') == 4
    files, pack = read_pack(input_path, config)
    v2 = config.get('editorial_version', 1) == 2
    speakers = infer_speakers(files) if v2 else None
    episodes, parse_errors = [], []
    for f in files:
        try:
            ep = parse(f, speakers, legacy=not v2); register(ep); episodes.append(ep)
        except ValueError as exc:
            parse_errors.append({'source': f['name'], 'error': str(exc)})
    if v4:
        episodes.sort(key=lambda ep:ep.episode_id)
    envelope=json.loads((run_dir/'promos.json').read_text())
    result = validate_envelope(envelope, episodes, config)
    if pack['pack_hash'] != manifest['pack_hash']:
        result['errors'].append({'errors': ['pack_hash_mismatch']})
    if envelope['pack_hash'] != pack['pack_hash'] or envelope['run_id'] != manifest['run_id'] or envelope['run_status'] != manifest['run_status']:
        result['errors'].append({'errors':['envelope_manifest_mismatch']})
    coverage = json.loads((run_dir/'coverage.json').read_text())
    if coverage['files_discovered'] != len(files) or coverage['episodes_parsed'] != len(episodes):
        result['errors'].append({'errors': ['coverage_counts_mismatch']})
    expected = {(f['name'], f['sha256']) for f in files}
    actual = {(e['source_name'], e['source_sha256']) for e in coverage['episodes']}
    if expected != actual or len(coverage['episodes']) != len(files):
        result['errors'].append({'errors': ['coverage_membership_mismatch']})
    if envelope['coverage'] != coverage:
        result['errors'].append({'errors':['envelope_coverage_mismatch']})
    # Reconstruct every exported candidate interval directly from the fresh source.
    ep_map={e.episode_id:e for e in episodes}
    interval_count=0
    reviewed_records = {}
    def check_record(record, label):
        nonlocal interval_count
        try:
            raw=record.get('candidate',record)
            cut=Candidate(**raw);ep=ep_map[cut.episode_id]
            problems=validate_cut(cut,ep,config)
            if cut.candidate_id != candidate(ep,cut.start_boundary_id,cut.end_boundary_id,cut.family_id,cut.variant).candidate_id:
                problems.append('candidate_identity_mismatch')
            start,end=coordinates(cut,ep)
            if 'start_ms' in record and (record['start_ms'],record['end_ms'],record['duration_ms'])!=(start,end,end-start):
                problems.append('candidate_coordinate_mismatch')
            if 'packet' in record:
                from .projection import project
                from .reviewer import validate_review
                from .schemas import REVIEW_SCHEMA
                packet,mapping=project(cut,ep,config['hook_window_ms'])
                if record['packet'] != packet or record['mapping'] != mapping:
                    problems.append('restricted_projection_mismatch')
                if v4:
                    from .editorial_v4 import (full_review_schema, opener_schema, ending_schema,
                        validate_full_review, validate_v4_opener, validate_v4_ending, editorial_result, ending_view)
                    full = record['full_review']
                    if full is not None:
                        check_schema(full, full_review_schema(packet)); validate_full_review(full, packet)
                    if record['opener'] is not None:
                        check_schema(record['opener'], opener_schema(packet)); validate_v4_opener(record['opener'], packet)
                    native = record['semantic_ending']
                    if native is not None:
                        if full is None:
                            raise ValueError('V4 ending cannot exist without a frozen full-review proposition')
                        question, proposition = full['unresolved_question']['text'], full['evidence_model']['proposition']
                        check_schema(native, ending_schema(packet, question, proposition))
                        validate_v4_ending(native, packet, question, proposition)
                    from .models import decode_response, request_key
                    from .editorial_v4 import opener_payload as v4_opener_payload
                    requests = [('full_review','v4/reviewer',packet,full_review_schema(packet)),
                                ('opener','v4/opener_review',v4_opener_payload(packet),opener_schema(packet))]
                    if full is not None:
                        question, proposition = full['unresolved_question']['text'], full['evidence_model']['proposition']
                        requests.append(('semantic_ending','v4/answer_check',
                            dict(packet,proposed_question=question,proposed_proposition=proposition),
                            ending_schema(packet,question,proposition)))
                    for field, role, payload, schema in requests:
                        if record.get(field) is None:
                            continue
                        prompt=(Path(__file__).parent/'prompts'/f'{role}.md').read_text()
                        key=request_key(role,payload,schema,prompt,config)
                        matching=[]
                        for path in (run_dir/'model_calls').glob(f'{key}-*.json'):
                            call=json.loads(path.read_text())
                            if call.get('validated') and decode_response(call['raw_text'])[0]==record[field]:
                                matching.append(call)
                        if not matching:
                            problems.append('v4_saved_response_provenance_mismatch:'+field)
                    if record['ending'] != ending_view(native):
                        problems.append('v4_ending_export_view_mismatch')
                    blocks = included(cut, ep)
                    expected = editorial_result(packet, full, record['opener'], native,
                        any(b.kind == 'action' and b.start_ms < start+3000 for b in blocks),
                        any(b.kind == 'ambiguous_tag' for b in blocks), config['minimum_hook_specificity'])
                    if expected != tuple(record[k] for k in ('review', 'tier', 'flags', 'eligibility_errors')):
                        problems.append('v4_editorial_gate_mismatch')
                    reviewed_records[cut.candidate_id] = record
                elif v2:
                    from .editorial import (ENDING_SCHEMA, OPENER_SCHEMA, editorial_result,
                                            opener_payload, validate_ending, validate_opener)
                    for field, schema, validate, payload in (
                        ('full_review', REVIEW_SCHEMA, validate_review, packet),
                        ('opener', OPENER_SCHEMA, validate_opener, opener_payload(packet)),
                        ('ending', ENDING_SCHEMA, validate_ending, packet),
                    ):
                        if record[field] is not None:
                            check_schema(record[field], schema)
                            validate(record[field], payload)
                    blocks = included(cut, ep)
                    expected = editorial_result(
                        packet, record['full_review'], record['opener'], record['ending'],
                        any(b.kind == 'action' and b.start_ms < start+3000 for b in blocks),
                        any(b.kind == 'ambiguous_tag' for b in blocks),
                        config.get('minimum_hook_specificity', 1),
                        gate_version=config.get('editorial_gate_version', 2))
                    if expected != tuple(record[k] for k in ('review','tier','flags','eligibility_errors')):
                        problems.append('editorial_gate_mismatch')
                    reviewed_records[cut.candidate_id] = record
                elif record.get('review'):
                    check_schema(record['review'],REVIEW_SCHEMA);validate_review(record['review'],packet)
                    if record['eligibility_errors'] != eligibility(record['review']):
                        problems.append('eligibility_mismatch')
            interval_count+=1
            if problems:result['errors'].append({'artifact':label,'candidate_id':cut.candidate_id,'errors':problems})
        except (ValueError,KeyError,TypeError) as exc:
            result['errors'].append({'artifact':label,'errors':[str(exc)]})
    provisional=json.loads((run_dir/'provisional.json').read_text())
    for c in provisional['base_candidates']:check_record(c,'provisional')
    for name in ['baseline_random.json','baseline_direct.json']:
        for r in json.loads((run_dir/name).read_text())['candidates']:check_record(r,name)
    family_records=[json.loads(s) for s in (run_dir/'candidate_families.jsonl').read_text().splitlines()]
    for f in family_records:
        for r in f['variants']:check_record(r,'candidate_families')
    if v4:
        from .analyst import analysis_schema, base_candidates, legacy_base_windows, validate_analysis
        from .discovery_v4 import deduplicate_events, menu
        from .schemas import plain
        menus={'version':4,'episodes':[menu(ep,config,legacy_base_windows(ep,config))[1] for ep in episodes]}
        if json.loads((run_dir/'discovery_menus.json').read_text())!=menus:
            result['errors'].append({'errors':['v4_discovery_menu_mismatch']})
        raw_bases=[]
        for row in provisional['analyses']:
            try:
                ep=ep_map[row['episode_id']]
                check_schema(row['analysis'],analysis_schema(ep,config));validate_analysis(row['analysis'],ep,config)
                raw_bases.extend(base_candidates(ep,row['analysis']))
            except (ValueError,KeyError) as exc:
                result['errors'].append({'errors':['v4_analysis_mismatch',str(exc)]})
        kept,_=deduplicate_events(raw_bases,ep_map)
        if provisional['base_candidates']!=[plain(c) for c,_ in kept]:
            result['errors'].append({'errors':['v4_event_group_mismatch']})
        extras=sum(max(0,len(f['variants'])-1) for f in family_records)
        if (len(family_records)>config['v4_initial_candidates'] or extras>config['v4_extra_reviews']
                or sum(len(f['variants'])>1 for f in family_records)>config['v4_extra_families']
                or any(len(f['variants'])>config['max_variants'] or
                    sum(r['candidate']['variant']=='combined_start_end' for r in f['variants'])>1 for f in family_records)
                or manifest['inference']['attempts']>config['max_attempts']
                or manifest['inference']['historical_reuses'] or len(files)>config['v4_episode_limit']):
            result['errors'].append({'errors':['v4_pilot_budget_or_reuse_violation']})
    result['all_artifact_intervals_checked']=interval_count
    import csv
    from .export import promo_row
    if v2:
        from .ranking import compare_family
        family_map = {f['family_id']: f for f in family_records}
        for promo in envelope['recommendations']:
            if config.get('editorial_gate_version', 2) >= 3:
                family = family_map[promo['family_id']]
                winner, comparison = compare_family(family['variants'])
                if winner is None or winner['candidate']['candidate_id'] != promo['promo_id'] or promo.get('boundary_decision') != comparison['decision']:
                    result['errors'].append({'errors': ['boundary_decision_mismatch'], 'promo_id': promo['promo_id']})
            row = reviewed_records.get(promo['promo_id'])
            if not row or row['tier'] != 'A' or (promo.get('hook_quote'), promo.get('ending_quote'), promo.get('question_status')) != (row['opener']['cue_quote'], row['ending']['ending_quote'], row['ending']['status']):
                result['errors'].append({'errors': ['export_editorial_mismatch'], 'promo_id': promo['promo_id']})
            if v4 and row:
                from .export import source_evidence_model
                expected_semantic = {'contract_version': 4,
                    'model': source_evidence_model(row['semantic_ending']['evidence_model'], row['mapping']),
                    'legacy_flags': row['review']['grounding']['legacy_flags'],
                    'resolved_flags': row['review']['grounding']['resolved_flags'],
                    'entailment_status': 'model_judgment_not_human_verified'}
                if promo.get('semantic_evidence') != expected_semantic:
                    result['errors'].append({'errors': ['v4_semantic_export_mismatch'], 'promo_id': promo['promo_id']})
    with (run_dir/'promos.csv').open(newline='') as stream:csv_rows=list(csv.DictReader(stream))
    if len(csv_rows) != len(envelope['recommendations']):
        result['errors'].append({'errors':['csv_count_mismatch']})
    for row,p in zip(csv_rows,envelope['recommendations']):
        if row != promo_row(p):
            result['errors'].append({'errors':['csv_json_mismatch'],'promo_id':p['promo_id']})
    import hashlib
    for line in (run_dir/'checksums.sha256').read_text().splitlines():
        sha, name = line.split('  ', 1)
        target = run_dir/name
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != sha:
            result['errors'].append({'errors': ['checksum_mismatch'], 'file': name})
    result['parse_errors'] = parse_errors
    result['valid'] = not result['errors'] and not parse_errors
    return result
