"""Read-only view model for Promo Lab Studio.

The Studio never re-runs inference, never recomputes gates and never edits a
run directory. It reads the files a run already saved (manifest, families,
promos, episodes, validation, failures) and reshapes them for display. Tiers,
eligibility errors, flags and boundary decisions are copied, not re-derived.
The only computation is the existing ``ranking.score`` weighted model score,
which the exports already use.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from .ranking import score
from .schemas import CRITERIA, WEIGHTS

STUDIO_VERSION = 1

CRITERION_LABELS = {
    'hook': 'Hook', 'cliffhanger': 'Cliffhanger', 'comprehension': 'Stands alone',
    'emotion': 'Emotion', 'coherence': 'Coherence', 'boundary': 'Clean boundaries',
}
GATE_THRESHOLDS = {'hook': 2, 'cliffhanger': 2, 'comprehension': 3, 'emotion': 0, 'coherence': 2, 'boundary': 2}
VARIANT_LABELS = {
    'base': 'Analyst cut', 'previous_start': 'Earlier start', 'next_start': 'Later start',
    'previous_end': 'Earlier end', 'next_end': 'Later end', 'combined_start_end': 'Both edges moved',
}

# code -> (short label, plain explanation, severity, area)
# severity: block = cannot be recommended; review = needs a human; info = context only
FLAGS = {
    'essential_context_missing': ('Needs earlier context', 'The reviewer said a new viewer would be missing setup they need to follow the cut.', 'block', 'context'),
    'no_specific_dialogue_hook': ('No specific hook', 'The isolated first-3-second check found no concrete threat, reveal, accusation or demand.', 'block', 'hook'),
    'no_complete_dialogue_hook': ('No complete line in 3 s', 'No full line of dialogue finishes inside the first three seconds, so the opener cannot be judged from speech.', 'review', 'hook'),
    'minimal_opener': ('Very short opener', 'The opening cue is only a word or two; an isolated verdict can overrate it.', 'review', 'hook'),
    'opener_unknown': ('Opener not verified', 'The opener check did not return a usable verdict.', 'review', 'hook'),
    'visual_hook_unverified': ('Visual opener', 'Timed action overlaps the opening. A visual hook may exist, but only dialogue was reviewed.', 'review', 'hook'),
    'question_answered': ('Ending is answered', 'The proposed cliffhanger question is answered inside the cut, so the ending does not leave viewers hanging.', 'block', 'ending'),
    'question_disputed': ('Ending disputed', 'The ending check disagreed with the proposed unresolved question.', 'review', 'ending'),
    'ending_unknown': ('Ending not verified', 'The separate ending check did not produce a validated verdict.', 'review', 'ending'),
    'question_terms_unverified': ('Question words not in dialogue', 'Key words of the proposed question do not appear in the quoted dialogue. Could be a paraphrase, could be invented.', 'review', 'grounding'),
    'entity_not_in_quote': ('Unquoted entity', 'A person or object named by the model is not in the quote it cited.', 'review', 'grounding'),
    'entity_mention_not_grounded': ('Entity not grounded', 'An entity mention could not be matched to the cited dialogue.', 'review', 'grounding'),
    'entity_uncertain_or_unsupported': ('Unsupported entity', 'The model marked an entity as uncertain or unsupported by the dialogue.', 'review', 'grounding'),
    'uncertain_roles': ('Roles unclear', 'Who is speaking to whom is not well supported by the dialogue.', 'review', 'grounding'),
    'speaker_addressee_binding_mismatch': ('Speaker/addressee mismatch', 'The claimed actor or target does not match the voices in the cited lines.', 'review', 'grounding'),
    'participant_uncertain': ('Participant uncertain', 'The model could not ground who the question is about.', 'review', 'grounding'),
    'reference_chain_uncertain': ('Reference unclear', 'A pronoun or reference could not be traced to an earlier line inside the cut.', 'review', 'grounding'),
    'stakes_evidence_missing': ('Stakes not cited', 'No dialogue evidence was cited for what is at stake.', 'review', 'grounding'),
    'speech_after_cited_ending': ('Speech after the ending', 'Dialogue continues after the line cited as the cliffhanger; the cut may end late.', 'review', 'ending'),
    'semantic_uncertainty': ('Model uncertain', 'The reviewer flagged its own reading as uncertain.', 'review', 'grounding'),
    'missing_context': ('Context notes', 'The reviewer listed context a new viewer might lack (not judged essential).', 'info', 'context'),
    'full_review_unavailable': ('Full review failed', 'The restricted full-cut review did not return a valid response.', 'block', 'process'),
    'analyst_quote_not_in_script': ('Analyst quote not in script', 'No matching normalized dialogue was found in this episode. A paraphrase or wording difference is not a verified quotation.', 'review', 'source'),
    'analyst_quote_outside_cut': ('Analyst quote outside cut', 'The analyst quoted a real line that is not inside this cut, so the cut cannot deliver what was described.', 'review', 'source'),
    'analyst_cited_id_mismatch': ('Cited ID is a different line', 'The block the analyst cited does not contain the line it quoted. A valid ID is not proof.', 'info', 'source'),
    'analyst_evidence_outside_cut': ('Cited evidence outside cut', 'Some lines the analyst cited fall outside this version of the cut.', 'info', 'source'),
    'opening_minimal': ('Weak spoken opener', 'The first spoken line is one or two words ("No.", "Ah!"). Any hook must come from visuals.', 'review', 'source'),
    'opening_none': ('No spoken opener', 'No dialogue in the cut to open on.', 'review', 'source'),
    'opening_line_not_complete_in_3s': ('First line runs past 3 s', 'The first spoken line does not finish inside the first three seconds.', 'info', 'source'),
    'answer_not_a_resolution': ('"Answered" by a request or possibility', 'Surface wording suggests a request, command, possibility or question. It may not resolve the fixed question; an editor must check the meaning. The exclusion reason is disputed, not disproved.', 'review', 'source'),
    'answer_restates_question': ('"Answered" by a restatement', 'The supposed answer only repeats the question\'s own words.', 'review', 'source'),
    'proposition_attempt_not_outcome': ('Attempt read as outcome', 'The question asks whether something works; the proposition only says someone is attempting it.', 'review', 'source'),
    'question_proposition_mismatch': ('Question and proposition differ', 'The proposed question and its proposition describe different events.', 'review', 'source'),
    'question_backward_or_background': ('Backward-looking question', 'The open question asks about background or the past, not a pending consequence.', 'info', 'source'),
    'dialogue_only': ('Dialogue only', 'Judged from dialogue text; visuals and performance were not seen.', 'info', 'process'),
    'script_timing_unverified_in_video': ('Script timing', 'Timestamps come from the screenplay and have not been checked against video.', 'info', 'process'),
}
CRITERION_FLAG = re.compile(r'^(hook|cliffhanger|comprehension|emotion|coherence|boundary)_(below_(\d)|unknown|uncited)$')
MODEL_NOTE = re.compile(r'^(\d+:\s*|uncertain:\s*)')


def explain_flag(code):
    """Return a display record for a saved flag or eligibility code."""
    code = str(code)
    if code in FLAGS:
        label, text, severity, area = FLAGS[code]
        return {'code': code, 'label': label, 'explanation': text, 'severity': severity, 'area': area}
    m = CRITERION_FLAG.match(code)
    if m:
        name = CRITERION_LABELS[m.group(1)]
        if m.group(3):
            return {'code': code, 'label': f'{name} below {m.group(3)}',
                    'explanation': f'The model scored {name.lower()} under the automatic gate of {m.group(3)}.',
                    'severity': 'block', 'area': 'score'}
        what = 'unknown' if m.group(2) == 'unknown' else 'not cited'
        return {'code': code, 'label': f'{name} {what}', 'explanation': f'The {name.lower()} judgment was {what}; uncited or unknown judgments cannot qualify.',
                'severity': 'block', 'area': 'score'}
    note = MODEL_NOTE.sub('', code).strip()
    return {'code': code, 'label': 'Model note', 'explanation': note or code, 'severity': 'info', 'area': 'model_note'}


def _lines(path):
    path = Path(path)
    if not path.is_file():
        return []
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s.strip()]


def _json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else default


def is_run_dir(path):
    path = Path(path)
    return (path/'run_manifest.json').is_file() and (path/'candidate_families.jsonl').is_file()


def _criteria(review, mapping):
    if not review or 'criteria' not in review:
        return None
    out = {}
    for k in CRITERIA:
        c = review['criteria'].get(k) or {}
        out[k] = {'score': c.get('score'), 'label': CRITERION_LABELS[k], 'weight': WEIGHTS[k], 'gate': GATE_THRESHOLDS[k],
                  'reason': c.get('reason', ''), 'evidence': [mapping.get(i, i) for i in c.get('evidence_ids', [])]}
    return out


def _claim(claim, mapping):
    if not isinstance(claim, dict):
        return None
    ids = claim.get('evidence_ids') or []
    return {'text': claim.get('text', ''), 'evidence': [mapping.get(i, i) for i in ids]}


def _ending(record, mapping):
    """Prefer the native V4 semantic ending, else the V2/V3 ending check."""
    sem = record.get('semantic_ending') or None
    end = record.get('ending') or None
    if not sem and not end:
        return None
    src = end or {}
    out = {
        'question': (sem or {}).get('question') or src.get('question', ''),
        'status': (sem or {}).get('status') or src.get('status'),
        'ending_quote': src.get('ending_quote', ''),
        'answer_quote': (sem or {}).get('answer_quote') or src.get('answer_quote', ''),
        'unresolved_consequence': (sem or {}).get('unresolved_consequence') or src.get('unresolved_consequence', ''),
        'context': (sem or {}).get('context') or src.get('context', ''),
        'uncertainty': src.get('uncertainty_reason', '') or (json.dumps((sem or {}).get('uncertainty'), ensure_ascii=False) if (sem or {}).get('uncertainty') else ''),
        'evidence': [mapping.get(i, i) for i in src.get('ending_evidence_ids', [])],
        'basis': 'v4_semantic_ending' if sem else 'v2_ending_check',
    }
    if sem and sem.get('proposed_proposition'):
        out['proposition'] = sem['proposed_proposition']
    return out


def _opener(op):
    if not op:
        return None
    return {k: op.get(k) for k in ('hook_present', 'cue_quote', 'cue_type', 'specificity', 'reason', 'uncertain', 'standalone') if k in op}


def _status(record, selected, has_tiers):
    review = record.get('review') or None
    tier = record.get('tier')
    if has_tiers:
        if review is None and tier is None:
            return 'failed'
        if tier == 'A':
            return 'recommended' if selected else 'accepted'
        return {'B': 'review', 'Excluded': 'excluded'}.get(tier, 'failed')
    if review is None:
        return 'failed'
    if record.get('eligibility_errors'):
        return 'excluded'
    return 'recommended' if selected else 'eligible'


FAMILY_ORDER = ['recommended', 'accepted', 'eligible', 'review', 'excluded', 'failed']


def _variant(record, selected_ids, has_tiers, winner_id, deltas, block_index):
    c = record['candidate']
    mapping = record.get('mapping') or {}
    review = record.get('review') or None
    crit = _criteria(review, mapping)
    s = score(review['criteria']) if review and 'criteria' in review else None
    codes = list(dict.fromkeys(list(record.get('eligibility_errors') or []) + list(record.get('flags') or [])))
    utterances = []
    for u in (record.get('packet') or {}).get('utterances', []):
        utterances.append({'id': u['id'], 'voice': u.get('voice'), 'text': u.get('text', ''),
                           'start_ms': u.get('start_ms'), 'end_ms': u.get('end_ms'),
                           'in_hook_window': bool(u.get('complete_in_opener')),
                           'source_block': mapping.get(u['id'])})
    status = _status(record, c['candidate_id'] in selected_ids, has_tiers)
    delta = next((d for d in deltas if d.get('against') == c['candidate_id']), None)
    return {
        'candidate_id': c['candidate_id'], 'variant': c.get('variant', 'base'),
        'variant_label': VARIANT_LABELS.get(c.get('variant'), c.get('variant', '').replace('_', ' ').title()),
        'start_ms': record['start_ms'], 'end_ms': record['end_ms'], 'duration_ms': record['duration_ms'],
        'start_boundary_id': c.get('start_boundary_id'), 'end_boundary_id': c.get('end_boundary_id'),
        'status': status, 'tier': record.get('tier'), 'is_winner': c['candidate_id'] == winner_id,
        'reviewed': review is not None, 'score': s, 'criteria': crit,
        'interpretation': _claim((review or {}).get('interpretation'), mapping),
        'unresolved_question': _claim((review or {}).get('unresolved_question'), mapping),
        'missing_context': list((review or {}).get('missing_context') or []),
        'essential_context_missing': (review or {}).get('essential_context_missing'),
        'opener': _opener(record.get('opener')), 'ending': _ending(record, mapping),
        'flags': [explain_flag(x) for x in codes],
        'delta_vs_winner': (delta or {}).get('criteria_winner_minus_alternative'),
        'blocks_only_in_winner': (delta or {}).get('added_block_ids', []),
        'blocks_only_here': (delta or {}).get('removed_block_ids', []),
        'utterances': utterances,
        'raw': {k: record.get(k) for k in ('review', 'opener', 'ending', 'semantic_ending', 'tier', 'flags', 'eligibility_errors') if k in record},
    }


def _block_view(block, include_source):
    b = {'id': block['block_id'], 'kind': block['kind'], 'label': block.get('label', ''),
         'start_ms': block['start_ms'], 'end_ms': block['end_ms'], 'scene_id': block.get('scene_id')}
    if include_source:
        b['speaker'] = block.get('speaker', '')
        b['text'] = block.get('text', '')
    return b


def _label(manifest, run_dir):
    base = _base_label(manifest)
    summary = _json(Path(run_dir)/'v5_summary.json')
    return base + f" + V{summary.get('source_check_version', 5)} source check" if summary else base


def _base_label(manifest):
    cfg = manifest.get('config', {})
    if cfg.get('discovery_version') == 4 or cfg.get('editorial_gate_version') == 4:
        version = 'V4'
    elif cfg.get('editorial_gate_version') == 3:
        version = 'V2 · gate 3'
    elif cfg.get('editorial_version') == 2:
        version = 'V2'
    else:
        version = 'V1'
    return version


def load_run(run_dir, include_source=True, label=None, note=None, synthetic=None):
    """Build the Studio view model from a saved run directory."""
    root = Path(run_dir)
    if not is_run_dir(root):
        raise ValueError(f'Not a Promo Lab run directory: {root}')
    manifest = _json(root/'run_manifest.json')
    families_raw = _lines(root/'candidate_families.jsonl')
    promos = _json(root/'promos.json', {'recommendations': []})
    validation = _json(root/'validation.json')
    coverage = _json(root/'coverage.json', {})
    failures = _lines(root/'failures.jsonl')
    rejections = _lines(root/'rejections.jsonl')
    episodes_raw = _lines(root/'episodes.jsonl')
    v5 = {b['candidate_id']: b for b in _lines(root/'v5_source_check.jsonl')}
    v5_audit = {a['family_id']: a for a in _lines(root/'v5_analyst_audit.jsonl')}
    v5_summary = _json(root/'v5_summary.json')
    if v5:
        for f in families_raw:
            for v in f.get('variants', []):
                b = v5.get(v['candidate']['candidate_id'])
                if b and b.get('v5_tier') and b['v5_tier'] != v.get('tier'):
                    v['original_tier'] = v.get('tier'); v['tier'] = b['v5_tier']
    has_tiers = any('tier' in v for f in families_raw for v in f.get('variants', []))
    selected_ids = {r['promo_id'] for r in promos.get('recommendations', [])}
    block_index = {b['block_id']: b for e in episodes_raw for b in e.get('blocks', [])}

    families = []
    for f in families_raw:
        comp = f.get('comparison') or {}
        winner_id = comp.get('winner_id')
        variants = [_variant(v, selected_ids, has_tiers, winner_id, comp.get('deltas', []), block_index) for v in f.get('variants', [])]
        for vv, raw in zip(variants, f.get('variants', [])):
            sc = v5.get(vv['candidate_id'])
            if sc:
                vv['source_check'] = {k: sc.get(k) for k in ('edges', 'ending_check', 'claims_vs_this_cut', 'repairs', 'original_tier', 'v5_tier')}
                known = {x['code'] for x in vv['flags']}
                vv['flags'] += [explain_flag(x) for x in sc.get('v5_flags', []) if x not in known]
        statuses = [v['status'] for v in variants]
        status = next((s for s in FAMILY_ORDER if s in statuses), 'failed')
        prop = f.get('proposal') or {}
        decision = comp.get('decision') or {}
        families.append({
            'family_id': f['family_id'], 'episode_id': f['episode_id'], 'status': status,
            'winner_id': winner_id, 'near_tie': comp.get('near_tie', False),
            'decision': {'summary': decision.get('summary') or comp.get('reason', ''),
                         'tie_break_only': decision.get('tie_break_only'),
                         'unreviewed_alternatives': decision.get('unreviewed_alternatives', 0),
                         'alternatives': decision.get('alternatives', [])},
            'proposal': {'event_summary': prop.get('event_summary', ''),
                         'hook': (prop.get('hook') or {}).get('text', ''),
                         'cliffhanger': (prop.get('cliffhanger') or {}).get('text', ''),
                         'required_context': prop.get('required_context', []),
                         'visual_flags': prop.get('visual_flags', []),
                         'start_boundary_id': prop.get('start_boundary_id'), 'end_boundary_id': prop.get('end_boundary_id')},
            'pruned': f.get('pruned', []),
            'analyst_audit': v5_audit.get(f['family_id']),
            'variants': variants,
        })
    family_by_winner = {f['winner_id']: f['family_id'] for f in families}
    family_of = {v['candidate_id']: f['family_id'] for f in families for v in f['variants']}

    recs = []
    for r in promos.get('recommendations', []):
        seg = r['segments'][0]
        recs.append({
            'rank': r['rank'], 'promo_id': r['promo_id'], 'family_id': family_of.get(r['promo_id'], r.get('family_id')),
            'episode_id': seg['source_episode'], 'start_ms': seg['start_ms'], 'end_ms': seg['end_ms'],
            'start_timecode': seg['start_timecode'], 'end_timecode': seg['end_timecode'], 'duration_ms': r['duration_ms'],
            'segments': r['segments'], 'hook': r['hook'], 'cliffhanger': r['cliffhanger'], 'why_it_works': r.get('why_it_works', ''),
            'hook_quote': r.get('hook_quote', ''), 'ending_quote': r.get('ending_quote', ''),
            'score': (r.get('ranking') or {}).get('score'), 'tier': r.get('tier'), 'tie_group': r.get('tie_group', ''),
            'boundary_summary': (r.get('boundary_decision') or {}).get('summary', ''),
            'human_review_status': r.get('human_review_status') or (r.get('quality') or {}).get('human_status', 'not_reviewed'),
            'flags': [explain_flag(x) for x in (r.get('quality') or {}).get('flags', [])],
        })

    fam_per_episode = Counter(f['episode_id'] for f in families)
    episodes = []
    cov_by_id = {e['episode_id']: e for e in coverage.get('episodes', [])}
    for e in episodes_raw:
        cov = cov_by_id.get(e['episode_id'], {})
        episodes.append({'episode_id': e['episode_id'], 'source_name': e.get('source_name'), 'extent_ms': e.get('extent_ms'),
                         'title': (e.get('metadata') or {}).get('title', '') if include_source else '',
                         'parse_status': cov.get('parse_status', 'parsed'), 'analysis_status': cov.get('analysis_status'),
                         'families': fam_per_episode.get(e['episode_id'], 0),
                         'blocks': [_block_view(b, include_source) for b in e.get('blocks', [])]})

    variants = [v for f in families for v in f['variants']]
    tally = Counter(v['status'] for v in variants)
    fam_tally = Counter(f['status'] for f in families)
    stats = manifest.get('statistics', {})
    inf = manifest.get('inference', {})
    cfg = manifest.get('config', {})
    model = manifest.get('model', {})
    flag_counts = Counter(fl['code'] for v in variants for fl in v['flags'] if fl['area'] != 'model_note')
    return {
        'studio_version': STUDIO_VERSION,
        'run': {
            'id': root.name, 'label': label or root.name, 'note': note or '', 'synthetic': synthetic,
            'version': _label(manifest, root), 'run_id': manifest.get('run_id'), 'status': manifest.get('run_status'),
            'mode': manifest.get('mode'), 'execution': manifest.get('execution'),
            'started_utc': manifest.get('started_utc'), 'finished_utc': manifest.get('finished_utc'),
            'elapsed_s': manifest.get('elapsed_s'), 'pack_hash': manifest.get('pack_hash'),
            'gate_version': manifest.get('editorial_gate_version', cfg.get('editorial_gate_version')),
            'replay_source_run_id': manifest.get('replay_source_run_id'),
            'human_evaluation': manifest.get('human_evaluation'), 'video_verified': manifest.get('video_verified', False),
            'model': {'name': model.get('model') or cfg.get('model'), 'sha256': model.get('model_sha256'),
                      'runtime': model.get('runtime_identity'), 'provider': model.get('provider')},
            'inference': {'fresh_attempts': inf.get('attempts', 0), 'cache_hits': inf.get('cache_hits', 0),
                          'replays': inf.get('replays', 0), 'historical_reuses': inf.get('historical_reuses', 0),
                          'request_elapsed_s': inf.get('request_elapsed_s'), 'usage': inf.get('usage')},
            'limits': {'min_s': cfg.get('min_duration_ms', 20000)/1000, 'max_s': cfg.get('max_duration_ms', 60000)/1000,
                       'hook_window_s': cfg.get('hook_window_ms', 3000)/1000, 'max_families': cfg.get('max_families'),
                       'max_variants': cfg.get('max_variants'), 'neighbor_radius_s': (cfg.get('neighbor_radius_ms') or 0)/1000},
            'has_tiers': has_tiers,
        },
        'counts': {
            'episodes': stats.get('files_parsed', len(episodes)), 'files': stats.get('files_discovered'),
            'blocks': stats.get('blocks'), 'block_types': stats.get('block_types', {}),
            'base_candidates': stats.get('base_candidates'), 'families_reviewed': len(families),
            'variants': len(variants), 'variants_reviewed': sum(v['reviewed'] for v in variants),
            'recommended': len(recs), 'variant_status': dict(tally), 'family_status': dict(fam_tally),
            'tiers': stats.get('candidate_tiers'), 'failures': len(failures),
        },
        'validation': {'valid': (validation or {}).get('valid'), 'errors': len((validation or {}).get('errors', [])),
                       'intervals_checked': (validation or {}).get('all_artifact_intervals_checked'),
                       'recommendations_checked': (validation or {}).get('recommendations_checked')} if validation else None,
        'recommendations': recs,
        'families': families,
        'episodes': episodes,
        'failures': [{'stage': x.get('stage'), 'candidate_id': x.get('candidate_id'), 'episode_id': x.get('episode_id'),
                      'error': str(x.get('error', ''))[:400]} for x in failures],
        'rejections': dict(Counter(_reason(r.get('reason')) for r in rejections)),
        'flag_counts': dict(flag_counts.most_common()),
        'glossary': {code: {'label': v[0], 'explanation': v[1], 'severity': v[2], 'area': v[3]} for code, v in FLAGS.items()},
        'source_included': include_source,
        'source_check_summary': v5_summary,
        'family_by_winner': family_by_winner,
    }


def _reason(reason):
    if isinstance(reason, list):
        return ', '.join(map(str, reason)) or 'unspecified'
    return str(reason or 'unspecified')


def discover_runs(roots):
    """Find saved run directories (one level deep) under each root."""
    found = []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        candidates = [root] if is_run_dir(root) else sorted(p for p in root.iterdir() if p.is_dir())
        for p in candidates:
            if is_run_dir(p):
                found.append(p.absolute())
    return found


def run_summary(run_dir, label=None, note=None, synthetic=None):
    m = _json(Path(run_dir)/'run_manifest.json')
    s = m.get('statistics', {})
    promos = _json(Path(run_dir)/'promos.json', {'recommendations': []})
    return {'id': Path(run_dir).name, 'label': label or Path(run_dir).name, 'note': note or '', 'synthetic': synthetic,
            'version': _label(m, run_dir), 'status': m.get('run_status'), 'execution': m.get('execution'),
            'finished_utc': m.get('finished_utc'), 'episodes': s.get('files_parsed'),
            'recommended': len(promos.get('recommendations', [])), 'tiers': s.get('candidate_tiers'),
            'families_reviewed': s.get('families_reviewed'), 'model': (m.get('model') or {}).get('model')}


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def assert_public_safe(run_dir, public_inputs):
    """Refuse to export a run unless every episode came from an allow-listed public file.

    The check compares the source SHA-256 recorded in the run with the hashes of
    files in ``public_inputs`` (for example tests/fixtures/synthetic). A run made
    from a confidential pack cannot pass, because its hashes are not in that set.
    """
    allowed = {sha256_file(p) for p in Path(public_inputs).iterdir() if p.is_file()}
    episodes = _lines(Path(run_dir)/'episodes.jsonl')
    if not episodes:
        raise ValueError('Run has no episodes to check')
    bad = [e['episode_id'] for e in episodes if e.get('source_sha256') not in allowed]
    if bad:
        raise ValueError(f'Refusing static export: episodes {bad} are not from the public input set {public_inputs}')
    return True


STATIC_FILES = ('index.html', 'app.css', 'app.js')
STATIC_RUN_FILES = ('promos.json', 'promos.csv', 'validation.json', 'run_manifest.json', 'candidate_families.jsonl',
                    'failures.jsonl', 'rejections.jsonl', 'report.html', 'v5_editor_briefs.csv', 'v5_summary.json')


def static_dir():
    return Path(__file__).resolve().parent/'studio'


def build_static(runs, out_dir, public_inputs):
    """Write a static, synthetic-only Studio site that needs no Python server.

    ``runs`` is a list of (run_dir, label, note). Each must pass assert_public_safe.
    """
    out = Path(out_dir)
    if out.exists():
        raise ValueError('Output exists; choose a new directory')
    payload = []
    for run_dir, label, note in runs:
        assert_public_safe(run_dir, public_inputs)
        view = load_run(run_dir, include_source=True, label=label, note=note, synthetic=True)
        view['run']['files'] = [n for n in STATIC_RUN_FILES if (Path(run_dir)/n).is_file()]
        payload.append(view)
    src = static_dir()
    out.mkdir(parents=True)
    for (run_dir, _, _), view in zip(runs, payload):
        target = out/'runs'/view['run']['id']
        target.mkdir(parents=True)
        for name in view['run']['files']:
            (target/name).write_bytes((Path(run_dir)/name).read_bytes())
    for name in STATIC_FILES:
        (out/name).write_bytes((src/name).read_bytes())
    fonts = src/'fonts'
    (out/'fonts').mkdir()
    for f in sorted(fonts.iterdir()):
        if f.is_file():
            (out/'fonts'/f.name).write_bytes(f.read_bytes())
    index = [{k: v for k, v in run_summary(r[0], r[1], r[2], True).items()} for r in runs]
    data = {'mode': 'static', 'runs': index, 'views': {p['run']['id']: p for p in payload}}
    (out/'data.js').write_text('window.KUKU_STATIC=' + json.dumps(data, ensure_ascii=False, separators=(',', ':')) + ';\n', encoding='utf-8')
    return {'out': str(out.resolve()), 'runs': [p['run']['id'] for p in payload],
            'files': sorted(str(p.relative_to(out)) for p in out.rglob('*') if p.is_file())}
