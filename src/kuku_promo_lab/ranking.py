import re
from collections import Counter

from .schemas import CRITERIA, WEIGHTS
from .timeline import coordinates, included


def score(criteria):
    if any(criteria[k]['score'] is None for k in CRITERIA):
        return None
    return sum(criteria[k]['score']*WEIGHTS[k] for k in CRITERIA)/4


def eligibility(review):
    errors = []
    for k, threshold in [('hook', 2), ('cliffhanger', 2), ('comprehension', 3), ('emotion', 0), ('coherence', 2), ('boundary', 2)]:
        c = review['criteria'][k]
        if c['score'] is None:
            errors.append(f'{k}_unknown')
        elif c['score'] < threshold:
            errors.append(f'{k}_below_{threshold}')
        if not c['evidence_ids']:
            errors.append(f'{k}_uncited')
    if review['essential_context_missing']:
        errors.append('essential_context_missing')
    return errors


def ordering(record):
    review = record.get('review')
    tier = {'A': 0, 'B': 1, 'Excluded': 2}.get(record.get('tier'), int(bool(record['eligibility_errors'])))
    if review is None:
        return (tier, 1, 0, 0, 0, record['duration_ms'], record['candidate']['candidate_id'])
    criteria = review['criteria']
    return (tier, -(score(criteria) or 0),
            -(criteria['comprehension']['score'] or 0), -(criteria['cliffhanger']['score'] or 0),
            -(criteria['hook']['score'] or 0), record['duration_ms'], record['candidate']['candidate_id'])


def boundary_decision(winner, base, reviewed, records):
    factors = ['tier', 'weighted_score', 'comprehension', 'cliffhanger',
               'hook', 'shorter_duration', 'candidate_id']
    alternatives = []
    for record in reviewed:
        if record is winner:
            continue
        different = [i for i, (a, b) in enumerate(zip(ordering(winner), ordering(record))) if a != b]
        factor = factors[different[0]] if different else 'candidate_id'
        alternatives.append({
            'candidate_id': record['candidate']['candidate_id'], 'variant': record['candidate']['variant'],
            'interval_ms': [record['start_ms'], record['end_ms']], 'tier': record.get('tier'),
            'decided_by': factor,
            'flags_absent_on_winner': sorted(set(record.get('flags', []))-set(winner.get('flags', [])))})
    tied = bool(alternatives) and all(a['decided_by'] in {'shorter_duration','candidate_id'} for a in alternatives)
    if not alternatives:
        summary = 'No nearby alternative cut could be reviewed; the boundary was not compared.'
    elif tied:
        summary = (f"All {len(alternatives)} reviewed nearby cuts received identical judgments; "
                   "this cut was chosen by the shorter-duration/ID tie-break, without an editorial difference.")
    else:
        counts = Counter(a['decided_by'] for a in alternatives)
        summary = f"Preferred over {len(alternatives)} nearby cuts. Deciding factors: " + ', '.join(
            f'{key} ({value})' for key,value in sorted(counts.items())) + '.'
        flags = Counter(flag for a in alternatives for flag in a['flags_absent_on_winner'])
        if flags:
            extra = ' Alternatives carried: ' + ', '.join(f'{key} ({value})' for key,value in sorted(flags.items())) + '.'
            if len(summary+extra) <= 300:
                summary += extra
    return {'winner_variant': winner['candidate']['variant'],
            'winner_interval_ms': [winner['start_ms'],winner['end_ms']],
            'base_interval_ms': [base['start_ms'],base['end_ms']] if base else None,
            'alternatives': alternatives, 'unreviewed_alternatives': sum(r.get('review') is None for r in records),
            'tie_break_only': tied, 'summary': summary}


def compare_family(records):
    ordered = sorted(records, key=ordering)
    reviewed = [r for r in ordered if r.get('review') is not None]
    if not reviewed:
        return None, {'reason': 'all_reviews_failed', 'deltas': [], 'near_tie': False}
    winner = reviewed[0]
    base = next((r for r in reviewed if r['candidate']['variant'] == 'base'), None)
    deltas = []
    for r in reviewed:
        if r is winner:
            continue
        criteria_delta = {}
        for k in CRITERIA:
            a, b = winner['review']['criteria'][k]['score'], r['review']['criteria'][k]['score']
            criteria_delta[k] = None if a is None or b is None else a-b
        wa, ra = set(winner['mapping'].values()), set(r['mapping'].values())
        deltas.append({'against': r['candidate']['candidate_id'], 'criteria_winner_minus_alternative': criteria_delta,
                       'added_block_ids': sorted(wa-ra), 'removed_block_ids': sorted(ra-wa)})
    s = score(winner['review']['criteria'])
    near = any(s is not None and score(r['review']['criteria']) is not None and
               abs(s-score(r['review']['criteria'])) < 5 and ordering(winner)[0] == ordering(r)[0] for r in reviewed[1:])
    return winner, {'reason': 'Eligibility, fixed weighted score, comprehension, cliffhanger, hook, shorter duration, candidate ID.',
                    'winner_id': winner['candidate']['candidate_id'], 'near_tie': near, 'deltas': deltas,
                    'base_id': base['candidate']['candidate_id'] if base else None,
                    'decision': boundary_decision(winner, base, reviewed, records)}


def tokens(text):
    return re.findall(r"[\w]+(?:['’][\w]+)*", text.casefold())


def text_similarity(a, b):
    a, b = tokens(a), tokens(b)
    if min(len(a), len(b)) < 5:
        return float(a == b and bool(a))
    aa = {tuple(a[i:i+5]) for i in range(len(a)-4)}
    bb = {tuple(b[i:i+5]) for i in range(len(b)-4)}
    return len(aa & bb)/len(aa | bb) if aa | bb else 0


def similarity(a, b):
    overlap = 0
    if a['candidate']['episode_id'] == b['candidate']['episode_id']:
        intersection = max(0, min(a['end_ms'], b['end_ms'])-max(a['start_ms'], b['start_ms']))
        overlap = intersection/(max(a['end_ms'], b['end_ms'])-min(a['start_ms'], b['start_ms']))
    return overlap, text_similarity(a['dialogue'], b['dialogue'])


def rank(records, top_k):
    selected, suppressed = [], []
    for r in sorted(records, key=ordering):
        if r['eligibility_errors']:
            continue
        matches = [(prior, *similarity(r, prior)) for prior in selected]
        duplicate = next(((p, i, t) for p, i, t in matches if r['candidate']['candidate_id'] == p['candidate']['candidate_id'] or i >= .7 or t >= .8), None)
        if duplicate:
            suppressed.append({'candidate_id': r['candidate']['candidate_id'], 'reason': 'near_duplicate',
                               'winner_id': duplicate[0]['candidate']['candidate_id'], 'interval_iou': duplicate[1], 'dialogue_jaccard': duplicate[2]})
            continue
        if len(selected) >= top_k:
            suppressed.append({'candidate_id': r['candidate']['candidate_id'], 'reason': 'top_k'})
            continue
        maxsim = max((max(i, t) for p, i, t in matches), default=0)
        r = dict(r, redundancy_score=2 if maxsim >= .3 else 3 if maxsim > 0 else 4)
        selected.append(r)
    return selected, suppressed
