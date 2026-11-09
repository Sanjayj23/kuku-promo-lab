"""V6 source check: deterministic, zero-call grounding of saved model outputs.

Why this exists (all observed in real saved runs, see the V4 source audit):
  * The analyst attaches invented dialogue to real block IDs; a valid ID proves
    where a line is, not that the explanation is true.
  * Openers such as "No." or "Ah!" reach expensive reviews.
  * The claimed cliffhanger line often lies outside the chosen cut.
  * Ending checks call a question "answered" by a request ("...accept my ring"),
    a command ("Burn the letters"), a possibility ("may help") or a restatement
    of the question itself.
  * Questions ask about background ("Did X truly...", "What herbs...") instead
    of a pending consequence; V4 question and proposition describe different events.

What it does:
  * Reads the real first and last spoken lines of every cut from a fresh parse of
    the source pack, so explanations shown to editors are quotations, not model prose.
  * Locates every quoted line in the analyst's text in the script: in the cut,
    outside it (and where), or nowhere.
  * Classifies the speech act of an "answer" and disputes resolutions it cannot support.
  * Suggests source-valid boundary repairs (substantive opener, reachable closing line).
    Suggestions are labelled unreviewed; they are not recommendations.

Guarantees: it never calls a model, never edits the input run, never creates Tier A,
and never removes an existing flag. Speech-act and question-overlap flags are advisory, not semantic truth.
Its only tier effect is that an Excluded cut whose
*sole* hard reason is a disputed "question answered" verdict is routed to human review.
"""
from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path

SOURCE_CHECK_VERSION = 6
HOOK_MS = 3000

STOP = set('''a an the and or but if then so to of in on at by for with from into onto as is are was were be been being am
i you he she it we they me him her us them my your his its our their this that these those there here what who whom whose
which when where why how not no do does did done have has had will would shall should can could may might must just
oh ah um uh hey yes yeah okay ok please'''.split())
INTERJECTIONS = {'no', 'ah', 'oh', 'what', 'wait', 'stop', 'yes', 'hey', 'huh', 'please', 'help', 'why', 'how', 'who', 'enough', 'now',
                 'sir', 'my lady', 'my lord', 'mother', 'father'}
IMPERATIVE_VERBS = set('''accept answer ask be believe bring burn call come close drink eat fetch find forget forgive get give go hand
help hold keep kill leave let listen look marry move open pay promise put release remember ring run save say send sign sit
speak stand stay stop swear take tell think trust wait walk watch'''.split())
MODAL = re.compile(r"\b(may|might|could|can|will|shall|would|should|must|going to|gonna|try|trying|attempt|attempting|hope|plan|intend|ll)\b")
OUTCOME_Q = re.compile(r"\b(succeed|success|effective|work|survive|save|saved|stop|escape|win|in time|recover|cure|live|die)\b")
ATTEMPT_P = re.compile(r"\b(attempt|attempting|tries|trying|try|plans?|intends?|preparing|prepares|about to)\b")
QUOTE = re.compile(r'["“]([^"”]{2,})["”]')


def norm(text):
    text = str(text).lower().replace('’', "'").replace('…', ' ')
    return re.sub(r'\s+', ' ', re.sub(r"[^\w\s']", ' ', text)).strip()


def tokens(text):
    return [t for t in norm(text).replace("'", ' ').split() if t]


def content(text):
    return [t for t in tokens(text) if t not in STOP and len(t) > 1]


def strip_quotes(text):
    return str(text).strip().strip('"“”').strip()


# ----------------------------------------------------------------- lines and openers
def spoken(episode):
    return [b for b in episode.blocks if b.kind == 'dialogue']


def lines_in(episode, start, end):
    return [b for b in spoken(episode) if b.start_ms >= start and b.end_ms <= end]


def opener_strength(text):
    """'none' | 'minimal' | 'substantive' for the first spoken line of a cut."""
    t = norm(strip_quotes(text))
    if not t:
        return 'none'
    words, ctoks = t.split(), content(t)
    if t in INTERJECTIONS or len(words) <= 2 or not ctoks:
        return 'minimal'
    return 'substantive'


def line_view(block, cut_start=None):
    if block is None:
        return None
    v = {'block_id': block.block_id, 'start_ms': block.start_ms, 'end_ms': block.end_ms,
         'speaker': block.speaker, 'text': strip_quotes(block.text)}
    if cut_start is not None:
        v['offset_ms'] = block.start_ms - cut_start
    return v


def edges(episode, start, end):
    inside = lines_in(episode, start, end)
    first = inside[0] if inside else None
    last = inside[-1] if inside else None
    in_window = [b for b in inside if b.end_ms <= start + HOOK_MS]
    return {
        'opening': line_view(first, start),
        'opening_complete_in_3s': bool(first and first.end_ms <= start + HOOK_MS),
        'opening_strength': opener_strength(first.text) if first else 'none',
        'hook_window_lines': [line_view(b, start) for b in in_window],
        'closing': line_view(last, start),
        'spoken_lines': len(inside),
    }


# ----------------------------------------------------------------- analyst claims
def locate(quote, episode, start=None, end=None):
    """Find the dialogue block that contains a quoted fragment.

    Exact whole-line matches win; then whole-word substring matches; then (for quotes of
    3+ words) contiguous whole-word fragments. No token-overlap fallback is used. Lines inside [start, end] are preferred at each level. Quotes of
    one or two words must match a whole line exactly, so "When?" never matches "And when...".
    """
    q = norm(strip_quotes(quote))
    qt = q.split()
    if not qt:
        return None
    lines = spoken(episode)
    inside = (lambda b: start is not None and start <= b.start_ms and b.end_ms <= end)

    def pick(cands):
        if not cands:
            return None
        return next((b for b in cands if inside(b)), cands[0])
    exact = [b for b in lines if norm(strip_quotes(b.text)) == q]
    if exact or len(qt) <= 2:
        return pick(exact)
    word = [b for b in lines if f' {q} ' in f' {norm(strip_quotes(b.text))} ']
    if word:
        return pick(word)
    return None


def audit_claims(proposal, episode, start, end):
    """Check every quoted line in the analyst's text against the script."""
    out = []
    for field in ('hook', 'cliffhanger'):
        claim = proposal.get(field) or {}
        text, ids = claim.get('text', ''), list(claim.get('evidence_ids') or [])
        # Single-quoted spans require non-word delimiters so contractions and possessives
        # inside a quote are retained rather than mistaken for its end.
        single = re.compile(r"(?<![\w])'(.+?)'(?![\w])|‘(.+?)’(?![\w])")
        quotes = QUOTE.findall(text)
        quotes += [next(g for g in m.groups() if g is not None) for m in single.finditer(text)]
        if not quotes:
            out.append({'field': field, 'quote': None, 'status': 'unquoted_description', 'text': text[:240], 'cited_ids': ids})
            continue
        for q in quotes:
            b = locate(q, episode, start, end)
            if b is None:
                status, where = 'not_in_script', None
            elif start <= b.start_ms and b.end_ms <= end:
                status, where = 'in_cut', b
            else:
                status, where = ('after_cut' if b.start_ms >= end else 'before_cut'), b
            rec = {'field': field, 'quote': strip_quotes(q)[:240], 'status': status, 'cited_ids': ids}
            if where is not None:
                rec['found_at'] = line_view(where)
                rec['cited_id_matches'] = where.block_id in ids
            else:
                rec['cited_id_matches'] = False
            out.append(rec)
    return out


def cited_outside(proposal, episode, start, end):
    """Cited evidence IDs (hook/cliffhanger) whose blocks lie outside the cut. Catches prose claims too."""
    blocks = {b.block_id: b for b in episode.blocks}
    out = []
    for field in ('hook', 'cliffhanger'):
        for i in (proposal.get(field) or {}).get('evidence_ids') or []:
            b = blocks.get(i)
            if b is None:
                out.append({'field': field, 'id': i, 'status': 'unknown_id'})
            elif not (start <= b.start_ms and b.end_ms <= end):
                out.append({'field': field, 'id': i, 'status': 'outside_cut', 'start_ms': b.start_ms, 'kind': b.kind})
    return out


def claim_flags(claims, cited=None):
    flags = set()
    if cited:
        flags.add('analyst_evidence_outside_cut')
    for c in claims:
        if c['status'] == 'not_in_script':
            flags.add('analyst_quote_not_in_script')
        elif c['status'] in ('after_cut', 'before_cut'):
            flags.add('analyst_quote_outside_cut')
        if c['quote'] and c['status'] != 'not_in_script' and not c['cited_id_matches']:
            flags.add('analyst_cited_id_mismatch')
    return sorted(flags)


# ----------------------------------------------------------------- endings
def speech_act(text):
    raw = strip_quotes(text)
    if re.match(r'^(if|unless)\b', norm(raw)):
        return 'conditional'
    # Strip only name-like vocatives, never first-person assertions such as "I accept,".
    m = re.match(r"^(?:(?:Lady|Lord|Sir|Captain|Doctor)\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?(?:\.\.\.|…|,)\s+", raw)
    if m and norm(m.group()).split()[0] not in {'i', 'we', 'he', 'she', 'they', 'yes', 'no', 'then'} | IMPERATIVE_VERBS:
        raw = raw[m.end():]
    t = norm(raw)
    if not t:
        return 'empty'
    if raw.rstrip().endswith('?'):
        return 'question'
    first = t.split()[0]
    # Present acceptance/refusal and evidential "can confirm" are assertions in form.
    # This classification does not establish their truth or that they answer the question.
    if re.search(r'[,;]\s*(if|unless)\b', raw, re.I):
        return 'conditional'
    if re.match(r"^(?:(?:yes|then)\s+)?(?:i|we) (?:accept|refuse|decline|agree|consent|reject)\b", t):
        return 'assertion'
    if re.match(r"^(?:i|we) can (?:confirm|verify|affirm) (?:that|the|this|it|he|she|they|your)\b", t):
        return 'assertion'
    if first in IMPERATIVE_VERBS or re.match(r"^(?:please\b|don't\b|do not\b)", t):
        return 'request_or_command'
    if MODAL.search(t):
        return 'possibility_or_intention'
    if re.match(r"^(i|we|he|she|they) (didn't|did not|never|wasn't|was not|am not|didn t)\b", t.replace("'", "'")):
        return 'denial'
    return 'assertion'


def question_kind(q):
    t = norm(q)
    if re.match(r"^(will|can|could|would|is .* going to|are .* going to|who will|what will|how will|when will|does .* survive|can .* stop)\b", t):
        return 'pending'
    if re.match(r"^(did|was|were|had|has|have|why|who is|who are|what is|what are|what specific|what did|is|are|does|do)\b", t):
        return 'background_or_past'
    return 'other'


def ending_check(ending, semantic=None):
    """Return (flags, details) for a saved ending verdict."""
    if not ending and not semantic:
        return [], {}
    e = dict(ending or {})
    if semantic:
        e.setdefault('question', semantic.get('question'))
        e['status'] = semantic.get('status') or e.get('status')
        e['answer_quote'] = semantic.get('answer_quote') or e.get('answer_quote')
    q, status, ans = e.get('question') or '', e.get('status'), e.get('answer_quote') or ''
    flags, det = [], {'question_kind': question_kind(q) if q else None,
                      'assessment_basis': 'advisory_surface_patterns_not_semantic_entailment'}
    if q and det['question_kind'] == 'background_or_past':
        flags.append('question_backward_or_background')
    if status == 'answered' and ans:
        act = speech_act(ans)
        det['answer_speech_act'] = act
        if act in {'question', 'conditional', 'request_or_command', 'possibility_or_intention'}:
            flags.append('answer_not_a_resolution')
        qa, aa = set(content(q)), set(content(ans))
        if len(aa) >= 2 and aa <= qa:
            flags.append('answer_restates_question')
    if semantic and semantic.get('proposed_proposition'):
        p = semantic['proposed_proposition']
        qa, pa = set(content(q)), set(content(p))
        overlap = len(qa & pa) / max(1, len(qa | pa))
        det['question_proposition_overlap'] = round(overlap, 2)
        if overlap < 0.15:
            flags.append('question_proposition_mismatch')
        if OUTCOME_Q.search(norm(q)) and ATTEMPT_P.search(norm(p)):
            flags.append('proposition_attempt_not_outcome')
    return flags, det


HARD_PREFIXES = ('hook_', 'cliffhanger_', 'comprehension_', 'emotion_', 'coherence_', 'boundary_')
HARD = {'question_answered', 'essential_context_missing', 'no_specific_dialogue_hook', 'no_complete_dialogue_hook',
        'full_review_unavailable', 'comprehension_below_3'}


def hard_reasons(record):
    errs = list(record.get('eligibility_errors') or [])
    return sorted({x for x in errs if x in HARD or (x.startswith(HARD_PREFIXES) and ('_below_' in x or x.endswith(('_unknown', '_uncited'))))})


def v5_tier(record, v5flags):
    tier = record.get('tier')
    if tier == 'A' or tier is None:
        return tier
    if tier == 'Excluded':
        hard = set(hard_reasons(record))
        disputed = {'answer_not_a_resolution', 'answer_restates_question', 'proposition_attempt_not_outcome'} & set(v5flags)
        if hard == {'question_answered'} and disputed:
            return 'B'
    return tier


# ----------------------------------------------------------------- repairs
def boundary_ok(episode, ms):
    b = episode.boundaries.get(f'{episode.episode_id}:t{ms:09d}')
    return bool(b and b['safe'])


def repairs(episode, start, end, e, claims, min_ms=20000, max_ms=60000, radius_ms=15000):
    out = []
    if e['opening_strength'] != 'substantive' or not e['opening_complete_in_3s']:
        cands = []
        for b in spoken(episode):
            if abs(b.start_ms - start) > radius_ms or b.start_ms == start:
                continue
            if not boundary_ok(episode, b.start_ms) or opener_strength(b.text) != 'substantive':
                continue
            if not (min_ms <= end - b.start_ms <= max_ms) or b.end_ms - b.start_ms > HOOK_MS:
                continue
            cands.append(b)
        if cands:
            b = min(cands, key=lambda x: abs(x.start_ms - start))
            out.append({'kind': 'substantive_opening', 'start_ms': b.start_ms, 'end_ms': end,
                        'reason': f"opens on a complete line with more lexical content (hook quality unverified) instead of a {e['opening_strength']} opening",
                        'new_opening': line_view(b)})
    for c in claims:
        if c['field'] == 'cliffhanger' and c['status'] == 'after_cut':
            b = c['found_at']
            new_end = b['end_ms']
            if boundary_ok(episode, new_end) and min_ms <= new_end - start <= max_ms:
                out.append({'kind': 'reach_claimed_ending', 'start_ms': start, 'end_ms': new_end,
                            'reason': 'extends the cut to the closing line the analyst actually described',
                            'new_closing': b})
            else:
                out.append({'kind': 'claimed_ending_unreachable', 'start_ms': start, 'end_ms': new_end,
                            'reason': 'the described closing line cannot be reached within 60 s on a safe boundary'})
    for r in out:
        r['status'] = 'suggested_not_model_reviewed'
    return out


# ----------------------------------------------------------------- run-level
def scene_of(episode, ms):
    for b in episode.blocks:
        if b.start_ms <= ms < b.end_ms:
            return b.scene_id
    return None


def check_run(episodes, run_dir):
    """Build briefs for every compared cut in a saved run. Returns (briefs, family_audits, summary)."""
    run_dir = Path(run_dir)
    ep_map = {e.episode_id: e for e in episodes}
    families = [json.loads(s) for s in (run_dir/'candidate_families.jsonl').read_text().splitlines() if s.strip()]
    briefs, audits = [], []
    for f in families:
        ep = ep_map[f['episode_id']]
        base = next((v for v in f['variants'] if v['candidate'].get('variant') == 'base'), f['variants'][0])
        claims = audit_claims(f.get('proposal') or {}, ep, base['start_ms'], base['end_ms'])
        cited = cited_outside(f.get('proposal') or {}, ep, base['start_ms'], base['end_ms'])
        audits.append({'family_id': f['family_id'], 'episode_id': f['episode_id'], 'event_summary': (f.get('proposal') or {}).get('event_summary', ''),
                       'claims': claims, 'cited_outside_cut': cited, 'flags': claim_flags(claims, cited),
                       'event_key': f"{f['episode_id']}:{scene_of(ep, base['start_ms'])}"})
        for v in f['variants']:
            e = edges(ep, v['start_ms'], v['end_ms'])
            vclaims = audit_claims(f.get('proposal') or {}, ep, v['start_ms'], v['end_ms'])
            eflags, edet = ending_check(v.get('ending'), v.get('semantic_ending'))
            flags = list(claim_flags(vclaims, cited_outside(f.get('proposal') or {}, ep, v['start_ms'], v['end_ms'])))
            if e['opening_strength'] != 'substantive':
                flags.append(f"opening_{e['opening_strength']}")
            if not e['opening_complete_in_3s']:
                flags.append('opening_line_not_complete_in_3s')
            flags += eflags
            briefs.append({
                'candidate_id': v['candidate']['candidate_id'], 'family_id': f['family_id'], 'episode_id': f['episode_id'],
                'variant': v['candidate'].get('variant'), 'start_ms': v['start_ms'], 'end_ms': v['end_ms'],
                'original_tier': v.get('tier'), 'v5_tier': v5_tier(v, flags), 'hard_reasons': hard_reasons(v),
                'edges': e, 'ending_check': edet, 'v5_flags': sorted(set(flags)),
                'claims_vs_this_cut': [{k: c[k] for k in ('field', 'quote', 'status')} for c in vclaims],
                'repairs': repairs(ep, v['start_ms'], v['end_ms'], e, vclaims),
            })
    summary = summarize(briefs, audits)
    return briefs, audits, summary


def audit_all_proposals(episodes, run_dir):
    """Audit every saved analyst proposal (all episodes), not only shortlisted events."""
    path = Path(run_dir)/'provisional.json'
    if not path.is_file():
        return []
    ep_map = {e.episode_id: e for e in episodes}
    rows = []
    for item in json.loads(path.read_text()).get('analyses', []):
        ep = ep_map.get(item.get('episode_id'))
        analysis = item.get('analysis') or {}
        if ep is None:
            continue
        for k, prop in enumerate(analysis.get('proposals') or []):
            try:
                start = ep.boundaries[prop['start_boundary_id']]['ms']; end = ep.boundaries[prop['end_boundary_id']]['ms']
            except KeyError:
                rows.append({'episode_id': ep.episode_id, 'proposal': k, 'status': 'invalid_boundary'}); continue
            claims = audit_claims(prop, ep, start, end); cited = cited_outside(prop, ep, start, end)
            e = edges(ep, start, end)
            rows.append({'episode_id': ep.episode_id, 'proposal': k, 'start_ms': start, 'end_ms': end,
                         'claims': [{x: c.get(x) for x in ('field', 'quote', 'status')} for c in claims],
                         'cited_outside_cut': cited, 'flags': claim_flags(claims, cited),
                         'opening_strength': e['opening_strength'], 'opening_complete_in_3s': e['opening_complete_in_3s']})
    return rows


def summarize(briefs, audits):
    claim_status = Counter(c['status'] for a in audits for c in a['claims'])
    flag_counts = Counter(f for b in briefs for f in b['v5_flags'])
    tiers_before = Counter(str(b['original_tier']) for b in briefs)
    tiers_after = Counter(str(b['v5_tier']) for b in briefs)
    changed = [b['candidate_id'] for b in briefs if b['original_tier'] != b['v5_tier']]
    return {
        'source_check_version': SOURCE_CHECK_VERSION, 'model_calls': 0,
        'quote_match_policy': 'normalized_exact_line_or_contiguous_fragment_3plus_words',
        'speech_act_policy': 'advisory_only; no proof of truth or question resolution',
        'families': len(audits), 'distinct_events_by_scene': len({a['event_key'] for a in audits}),
        'cut_variants': len(briefs),
        'analyst_claims': dict(claim_status),
        'families_with_quote_not_in_script': sum('analyst_quote_not_in_script' in a['flags'] for a in audits),
        'families_with_quote_outside_cut': sum('analyst_quote_outside_cut' in a['flags'] for a in audits),
        'openings': dict(Counter(b['edges']['opening_strength'] for b in briefs)),
        'openings_not_complete_in_3s': sum(not b['edges']['opening_complete_in_3s'] for b in briefs),
        'flag_counts': dict(flag_counts.most_common()),
        'tiers_before': dict(tiers_before), 'tiers_after': dict(tiers_after),
        'tier_changes': changed, 'tier_A_created': sum(b['v5_tier'] == 'A' and b['original_tier'] != 'A' for b in briefs),
        'repair_suggestions': dict(Counter(r['kind'] for b in briefs for r in b['repairs'])),
    }


def write_briefs_csv(path, briefs):
    """One row per cut an editor should look at (V5 Tier A/B), quotations taken from the script."""
    import csv
    from .export import csv_safe
    tc = lambda ms: f"{int(ms//60000):02d}:{(ms % 60000)/1000:06.3f}"
    head = ['episode', 'candidate_id', 'variant', 'start', 'end', 'duration_s', 'original_tier', 'v5_tier',
            'first_line', 'first_line_complete_in_3s', 'opening_strength', 'last_line', 'ending_answer_act',
            'source_check_flags', 'suggested_repairs_unreviewed']
    with Path(path).open('w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh); w.writerow(head)
        for b in briefs:
            if b['v5_tier'] not in ('A', 'B'):
                continue
            e = b['edges']
            w.writerow([csv_safe(x) for x in [b['episode_id'], b['candidate_id'], b['variant'], tc(b['start_ms']), tc(b['end_ms']),
                        round((b['end_ms']-b['start_ms'])/1000, 3), b['original_tier'], b['v5_tier'],
                        (e['opening'] or {}).get('text', ''), e['opening_complete_in_3s'], e['opening_strength'],
                        (e['closing'] or {}).get('text', ''), b['ending_check'].get('answer_speech_act', ''),
                        '; '.join(b['v5_flags']),
                        '; '.join(f"{r['kind']} {tc(r['start_ms'])}-{tc(r['end_ms'])}" for r in b['repairs'])]])


def load_source(input_path, run_dir):
    """Fresh parse of the source pack, refusing a pack that does not match the run."""
    from .ingest import read_pack
    from .parser import infer_speakers, parse
    from .timeline import register
    manifest = json.loads((Path(run_dir)/'run_manifest.json').read_text())
    config = manifest['config']
    files, pack = read_pack(input_path, config)
    if pack['pack_hash'] != manifest['pack_hash']:
        raise ValueError('Source pack does not match this run (pack_hash mismatch)')
    v2 = config.get('editorial_version', 1) == 2
    speakers = infer_speakers(files) if v2 else None
    episodes = []
    for f in files:
        ep = parse(f, speakers, legacy=not v2); register(ep); episodes.append(ep)
    return episodes


def write_check(input_path, run_dir, out_dir, copy_run=True):
    """Write a NEW directory: an unedited copy of the run plus v5_* files."""
    run_dir, out = Path(run_dir), Path(out_dir)
    if out.exists():
        raise ValueError('Output exists; choose a new directory')
    episodes = load_source(input_path, run_dir)
    briefs, audits, summary = check_run(episodes, run_dir)
    if copy_run:
        shutil.copytree(run_dir, out)
    else:
        out.mkdir(parents=True)
    with (out/'v5_source_check.jsonl').open('w', encoding='utf-8') as fh:
        for b in briefs:
            fh.write(json.dumps(b, ensure_ascii=False)+'\n')
    with (out/'v5_analyst_audit.jsonl').open('w', encoding='utf-8') as fh:
        for a in audits:
            fh.write(json.dumps(a, ensure_ascii=False)+'\n')
    write_briefs_csv(out/'v5_editor_briefs.csv', briefs)
    proposals = audit_all_proposals(episodes, run_dir)
    if proposals:
        with (out/'v5_all_analyst_proposals.jsonl').open('w', encoding='utf-8') as fh:
            for r in proposals:
                fh.write(json.dumps(r, ensure_ascii=False)+'\n')
        summary['all_proposals'] = {'proposals': len(proposals),
            'claim_status': dict(Counter(c['status'] for r in proposals for c in r.get('claims', []))),
            'with_quote_not_in_script': sum('analyst_quote_not_in_script' in r.get('flags', []) for r in proposals),
            'with_quote_outside_cut': sum('analyst_quote_outside_cut' in r.get('flags', []) for r in proposals),
            'with_evidence_outside_cut': sum('analyst_evidence_outside_cut' in r.get('flags', []) for r in proposals),
            'opening_minimal_or_none': sum(r.get('opening_strength') in ('minimal', 'none') for r in proposals)}
    summary['source_run'] = str(run_dir.name)
    summary['note'] = ('Revision 6 deterministic source check over saved responses. Zero model calls. Compatibility filenames retain the v5_ prefix. Original run files are '
                       'byte-identical copies; v5_* files are additions. v5_tier never creates Tier A.')
    (out/'v5_summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False)+'\n')
    return summary
