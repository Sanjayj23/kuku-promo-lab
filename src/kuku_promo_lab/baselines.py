import random

from .ranking import similarity
from .schemas import plain
from .search import provisional_score
from .timeline import candidate, coordinates, valid_windows


def random_valid(episodes, config):
    rng = random.Random(config['seed'])
    result, missing = [], []
    for ep in episodes:
        windows = list(valid_windows(ep, config))
        if not windows:
            missing.append({'episode_id': ep.episode_id, 'reason': 'no_valid_window'})
            continue
        start, end = rng.choice(windows)
        c = candidate(ep, start, end, 'random-'+ep.episode_id, 'random')
        s, e = coordinates(c, ep)
        result.append({'candidate': plain(c), 'start_ms': s, 'end_ms': e, 'duration_ms': e-s,
                       'evaluation_basis': 'random_structural_only', 'semantic_review': None})
    rng.shuffle(result)
    for i, r in enumerate(result, 1):
        r['rank'] = i
    return result, missing


def direct(bases, episode_map, config):
    """Same actual analyst calls, scores and evidence; no neighbor search/restricted review."""
    ordered = sorted(bases, key=lambda x: (-provisional_score(x[1]), x[0].candidate_id))
    selected, suppressed, seen = [], [], set()
    for cut, proposal in ordered:
        ep = episode_map[cut.episode_id]
        start, end = coordinates(cut, ep)
        speech = [b.text for b in ep.blocks if b.kind == 'dialogue' and start <= b.start_ms and b.end_ms <= end]
        record = {'candidate': plain(cut), 'start_ms': start, 'end_ms': end, 'duration_ms': end-start,
                  'dialogue': '\n'.join(speech), 'proposal': proposal, 'score': provisional_score(proposal),
                  'evaluation_basis': 'story_aware_direct_llm', 'semantic_review': None}
        duplicate = next((r for r in selected if similarity(record, r)[0] >= .7 or similarity(record, r)[1] >= .8), None)
        if duplicate or cut.candidate_id in seen:
            suppressed.append({'candidate_id': cut.candidate_id, 'reason': 'near_duplicate'})
        elif len(selected) < config['top_k']:
            record['rank'] = len(selected)+1
            selected.append(record)
            seen.add(cut.candidate_id)
    return selected, suppressed
