from __future__ import annotations

import collections
import datetime
import importlib.metadata
import json
import json
import platform
import re
import sys
import time
from pathlib import Path

from .analyst import analyze, base_candidates
from .baselines import direct, random_valid
from .context import story_index
from .export import checksums, promos_csv, recommendation, write_json, write_jsonl
from .ingest import InputError, read_pack
from .models import ModelClient
from .parser import infer_speakers, parse
from .editorial import check_ending, check_opener, editorial_result
from .projection import project
from .ranking import compare_family, eligibility, rank, score
from .reviewer import review
from .schemas import digest, plain
from .search import shortlist, variants
from .timeline import coordinates, included, register, validate_cut
from .validation import validate_envelope


def run(input_path, out, config, replay_dir=None, mode='proposed', progress=None, prior_dir=None):
    out = Path(out)
    if out.exists():
        raise InputError(f'Output already exists; choose a new immutable run directory: {out}')
    files, pack = read_pack(input_path, config)
    v2 = config.get("editorial_version", 1) == 2
    v4 = config.get('editorial_gate_version') == 4
    if v4:
        if config.get('discovery_version') != 4 or not v2:
            raise InputError('V4 requires explicit discovery 4 and editorial 2')
        if prior_dir or config.get('prior_review'):
            raise InputError('V4 cannot reuse incompatible historical analyst/reviewer records')
        if not replay_dir and mode != 'random' and config['provider'] == 'local' and (
                not re.fullmatch(r'[a-f0-9]{64}',config['model_sha256'])
                or config['runtime_identity'] in {'unknown','user-supplied',''}):
            raise InputError('V4 live inference requires an exact checkpoint SHA256 and identified runtime')
        if len(files) > config['v4_episode_limit']:
            raise InputError('V4 pilot episode limit exceeded; full traversal requires a separately approved configuration')
        if not (1 <= config['v4_initial_candidates'] <= min(24, config['max_families']) and 0 <= config['v4_extra_reviews'] <= 24
                and 0 <= config['v4_extra_families'] <= 8 and 1 <= config['max_variants'] <= 4
                and config['max_proposals_per_episode'] <= 2 and config['max_base_windows'] <= 48):
            raise InputError('V4 bounded review policy exceeded')
    speakers = infer_speakers(files) if v2 else None
    if prior_dir:
        prior_dir = Path(prior_dir)
        prior = json.loads((prior_dir/'run_manifest.json').read_text())
        if (not v2 or prior['config'].get('editorial_version', 1) != 1
                or prior['pack_hash'] != pack['pack_hash'] or prior['run_status'] != 'complete'):
            raise InputError('Historical reuse requires a completed V1 run of the exact same source pack')
        keys = ['max_base_windows', 'max_proposals_per_episode', 'max_families', 'max_variants',
                'neighbor_radius_ms', 'min_duration_ms', 'max_duration_ms', 'hook_window_ms']
        config = dict(config, **{key: prior['config'][key] for key in keys})
        config['prior_review'] = {'run_id': prior['run_id'], 'config': prior['config']}
    package_dir = Path(__file__).parent
    code_files = sorted(p for p in package_dir.rglob('*') if p.suffix in {'.py', '.json', '.md'})
    code_identities = {str(p.relative_to(package_dir)): digest(p.read_bytes()) for p in code_files}
    out.mkdir(parents=True)
    started = datetime.datetime.now(datetime.timezone.utc)
    clock = time.perf_counter()
    run_id = ('run-v4-' if v4 else 'run-v2-' if v2 else 'run-')+started.strftime('%Y%m%dT%H%M%S')+'-'+digest([pack['pack_hash'], str(out)])[:8]
    episodes, coverage_records, failures, rejections = [], [], [], []
    for f in files:
        entry = {'episode_id': None, 'source_name': f['name'], 'source_sha256': f['sha256'],
                 'parse_status': 'failed', 'analysis_status': 'not_requested', 'error': None}
        try:
            ep = parse(f, speakers, legacy=not v2)
            if any(e.episode_id == ep.episode_id for e in episodes):
                raise InputError(f'Duplicate episode ID: {ep.episode_id}')
            register(ep); episodes.append(ep)
            entry.update(episode_id=ep.episode_id, parse_status='parsed')
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            entry['error'] = str(exc)
            entry['error_type'] = type(exc).__name__
            failures.append({'stage': 'parse', 'source': f['name'], 'error': str(exc),
                             'error_type': type(exc).__name__})
        coverage_records.append(entry)
    # Duplicate IDs must never be analyzed in an ambiguous source registry.
    if any('Duplicate episode ID' in f['error'] for f in failures):
        raise InputError('Duplicate episode identifiers; rename/repair input before inference')
    episodes.sort(key=lambda e: e.episode_id)
    ep_map = {e.episode_id: e for e in episodes}
    write_jsonl(out/'episodes.jsonl', [plain(e) for e in episodes])
    write_json(out/'input_inventory.json', pack)
    index = story_index(episodes)
    write_json(out/'story_index.json', index)
    if v4:
        from .analyst import legacy_base_windows
        from .discovery_v4 import menu
        write_json(out/'discovery_menus.json', {'version': 4, 'episodes': [
            menu(ep, config, legacy_base_windows(ep, config))[1] for ep in episodes]})
    client = ModelClient(config, out, replay_dir, prior_dir)
    bases, analyses = [], []
    if mode != 'random':
        for ep in episodes:
            entry = next(e for e in coverage_records if e['episode_id'] == ep.episode_id)
            if progress:
                progress(f'Analyzing {ep.episode_id}')
            try:
                analysis = analyze(ep, index, config, client)
                analyses.append({'episode_id': ep.episode_id, 'analysis': analysis})
                bases.extend(base_candidates(ep, analysis))
                entry['analysis_status'] = 'analyzed' if analysis['proposals'] else 'no_candidate'
            except (ValueError, RuntimeError, OSError) as exc:
                entry.update(analysis_status='failed', error=str(exc))
                failures.append({'stage': 'analyst', 'episode_id': ep.episode_id, 'error': str(exc)})
            write_json(out/'progress.json', {'stage': 'analysis', 'episode': ep.episode_id, 'completed': len(analyses), 'failures': len(failures)})
    family_logs, winners = [], []
    discovery_audit = None
    if v4 and mode == 'proposed':
        from .pipeline_v4 import search_reviews
        bases, family_logs, winners, decisions, discovery_audit = search_reviews(
            bases, ep_map, config, client, failures, progress)
        rejections.extend(decisions)
        write_json(out/'discovery_audit.json', discovery_audit)
    elif v4:
        from .discovery_v4 import deduplicate_events
        raw_count = len(bases)
        bases, duplicates = deduplicate_events(bases, ep_map)
        rejections.extend(duplicates)
        discovery_audit = {'version': 4, 'raw_proposals': raw_count,
            'provisional_event_groups': len(bases), 'duplicate_proposals': duplicates,
            'initial_reviews': 0, 'extra_reviews': 0, 'source_valid_review_visits': 0,
            'event_group_status': 'provisional_not_human_verified'}
        write_json(out/'discovery_audit.json', discovery_audit)
    if mode == 'proposed' and not v4:
        selected, pruned = shortlist(bases, config)
        rejections.extend(pruned)
        for base, proposal in selected:
            ep = ep_map[base.episode_id]
            cuts, pruned = variants(base, ep, config)
            records = []
            for cut in cuts:
                start, end = coordinates(cut, ep)
                packet, mapping = project(cut, ep, config['hook_window_ms'])
                r = {'candidate': plain(cut), 'start_ms': start, 'end_ms': end, 'duration_ms': end-start,
                     'packet': packet, 'mapping': mapping, 'dialogue': '\n'.join(u['text'] for u in packet['utterances']),
                     'review': None, 'eligibility_errors': ['review_not_performed']}
                if progress:
                    progress(f'Reviewing {base.family_id} / {cut.variant}')
                try:
                    if not v2 or packet['utterances']:
                        r['review'] = review(packet, client)
                        r['eligibility_errors'] = eligibility(r['review'])
                except (ValueError, RuntimeError, OSError) as exc:
                    r['error'] = str(exc)
                    failures.append({'stage': 'reviewer', 'candidate_id': cut.candidate_id, 'error': str(exc)})
                if v2:
                    checks = {'opener': None, 'ending': None}
                    for field, check in [('opener', check_opener), ('ending', check_ending)]:
                        try:
                            if field == 'ending':
                                question = r['review']['unresolved_question']['text'] if r['review'] else None
                                checks[field] = check(packet, client, question)
                            else:
                                checks[field] = check(packet, client)
                        except (ValueError, RuntimeError, OSError) as exc:
                            failures.append({'stage': field, 'candidate_id': cut.candidate_id, 'error': str(exc)})
                    source_blocks = included(cut, ep)
                    visual = any(b.kind == 'action' and b.start_ms < start+3000 for b in source_blocks)
                    ambiguous = any(b.kind == 'ambiguous_tag' for b in source_blocks)
                    r['full_review'] = r['review']
                    r.update(checks)
                    r['review'], r['tier'], r['flags'], r['eligibility_errors'] = editorial_result(
                        packet, r['full_review'], r['opener'], r['ending'], visual, ambiguous,
                        config.get('minimum_hook_specificity', 1),
                        gate_version=config.get('editorial_gate_version', 2))
                if r['eligibility_errors']:
                    rejections.append({'candidate_id': cut.candidate_id, 'family_id': base.family_id, 'reason': r['eligibility_errors']})
                records.append(r)
            winner, comparison = compare_family(records)
            if winner:
                winners.append(winner)
            family_logs.append({'family_id': base.family_id, 'episode_id': ep.episode_id, 'proposal': proposal,
                                'variants': records, 'pruned': pruned, 'comparison': comparison})
            write_json(out/'progress.json', {'stage': 'review', 'families_completed': len(family_logs), 'failures': len(failures)})
    ranked, suppressions = rank(winners, config['top_k'])
    rejections.extend(suppressions)
    near = {f['family_id']: f['comparison'].get('near_tie', False) for f in family_logs}
    comparisons = {f['family_id']: f['comparison'] for f in family_logs}
    recommendations = [recommendation(
        r, i, ep_map[r['candidate']['episode_id']], near[r['candidate']['family_id']],
        comparison=comparisons[r['candidate']['family_id']],
        gate_version=config.get('editorial_gate_version', 2)) for i, r in enumerate(ranked, 1)]
    failed_episodes = sum(e['parse_status'] == 'failed' or e['analysis_status'] == 'failed' for e in coverage_records)
    coverage = {'files_discovered': len(files), 'episodes_parsed': len(episodes),
                'episodes_analyzed': sum(e['analysis_status'] in {'analyzed', 'no_candidate'} for e in coverage_records),
                'episodes_failed': failed_episodes, 'episodes': coverage_records}
    status = 'partial' if failures else 'complete'
    if not episodes:
        status = 'failed'
    envelope = {'schema_version': '1.0', 'run_id': run_id, 'pack_hash': pack['pack_hash'], 'run_status': status,
                'coverage': coverage, 'recommendations': recommendations}
    validation = validate_envelope(envelope, episodes, config)
    if v2:
        validation['ambiguous_tags'] = [
            dict(w, episode_id=e.episode_id) for e in episodes for w in e.warnings if w['code'] == 'ambiguous_tag']
    if not validation['valid']:
        envelope['run_status'] = status = 'failed'
        failures.append({'stage': 'export_validation', 'errors': validation['errors']})
    random_rows, random_missing = random_valid(episodes, config)
    direct_rows, direct_suppressed = direct(bases, ep_map, config)
    write_json(out/'baseline_random.json', {'basis': 'seeded_random_structural_only', 'seed': config['seed'], 'candidates': random_rows, 'no_window': random_missing})
    write_json(out/'baseline_direct.json', {'basis': 'same_genuine_analyst_calls_no_restricted_review_no_search',
               'episodes_analyzed': coverage['episodes_analyzed'], 'candidates': direct_rows, 'suppressions': direct_suppressed,
               'cost_scope': 'Analyst calls are shared, not duplicated; usage in manifest.'})
    write_json(out/'promos.json', envelope)
    promos_csv(out/'promos.csv', recommendations, v2=v2)
    write_json(out/'coverage.json', coverage)
    write_json(out/'validation.json', validation)
    write_json(out/'provisional.json', {'status': 'analyst_only_not_restricted_reviewed', 'analyses': analyses,
                                       'base_candidates': [plain(c) for c, p in bases]})
    write_jsonl(out/'candidate_families.jsonl', family_logs)
    write_jsonl(out/'rejections.jsonl', rejections)
    write_jsonl(out/'failures.jsonl', failures)
    manifest = {'schema_version': '1.0', 'run_id': run_id, 'run_status': status, 'mode': mode,
                'execution': 'replay' if replay_dir else config['provider'], 'started_utc': started.isoformat(),
                'finished_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'elapsed_s': round(time.perf_counter()-clock, 4),
                'pack_hash': pack['pack_hash'], 'input_path': str(Path(input_path).resolve()), 'config': config,
                'config_hash': digest(config), 'code_hash': digest(sorted(code_identities.items())),
                'code_files': code_identities,
                'python': sys.version, 'platform': platform.platform(),
                'dependencies': {k: importlib.metadata.version(k) for k in ['jsonschema']},
                'model': {k: config[k] for k in ['provider', 'model', 'model_sha256', 'runtime_identity', 'temperature', 'top_p', 'top_k_sampling', 'max_tokens']},
                'inference': {'attempts': client.attempts, 'cache_hits': client.cache_hits, 'replays': client.replays, 'historical_reuses': client.historical_reuses,
                              'usage': client.usage, 'request_elapsed_s': round(client.latency_s, 4), 'calls': client.records},
                'statistics': {'files_discovered': len(files), 'files_parsed': len(episodes), 'scenes': sum(len(e.scenes) for e in episodes),
                               'blocks': sum(len(e.blocks) for e in episodes), 'block_types': dict(collections.Counter(b.kind for e in episodes for b in e.blocks)),
                               'total_extent_ms': sum(e.extent_ms for e in episodes), 'warnings': [dict(w, episode_id=e.episode_id) for e in episodes for w in e.warnings],
                               'base_candidates': len(bases), 'families_reviewed': len(family_logs), 'variants_reviewed': sum(r['review'] is not None for f in family_logs for r in f['variants']),
                               'recommendations': len(recommendations), 'failures': len(failures)},
                'context_policy': 'Current plus adjacent episode source metadata; no model outline; never sent to restricted reviewer.',
                'human_evaluation': 'pending', 'video_verified': False}
    if v2:
        manifest['statistics']['candidate_tiers'] = dict(collections.Counter(
            r['tier'] for f in family_logs for r in f['variants']))
    manifest['editorial_gate_version'] = config.get('editorial_gate_version', 2)
    if v4:
        manifest['discovery_version'] = 4
        manifest['statistics']['v4_discovery'] = {k: discovery_audit[k] for k in
            ['raw_proposals', 'provisional_event_groups', 'initial_reviews', 'extra_reviews', 'source_valid_review_visits']}
    if replay_dir:
        manifest['replay_source_run_id'] = json.loads((Path(replay_dir)/'run_manifest.json').read_text()).get('run_id')
    write_json(out/'run_manifest.json', manifest)
    # progress is a transient diagnostic and is finalized before checksumming.
    write_json(out/'progress.json', {'stage': 'finished', 'run_status': status})
    from .report import render_report
    render_report(out)
    checksums(out)
    return manifest
