from .schemas import WEIGHTS
from .timeline import candidate, coordinates, validate_cut


def provisional_score(proposal):
    return sum(proposal['scores'][k]*w for k, w in WEIGHTS.items())/4


def shortlist(bases, config):
    ordered = sorted(bases, key=lambda x: (-provisional_score(x[1]), x[0].family_id))
    first, rest, seen = [], [], set()
    for item in ordered:
        if item[0].episode_id in seen:
            rest.append(item)
        else:
            first.append(item)
            seen.add(item[0].episode_id)
    selected = (first+rest)[:config['max_families']]
    ids = {x[0].family_id for x in selected}
    return selected, [{'family_id': c.family_id, 'reason': 'family_budget'} for c, p in ordered if c.family_id not in ids]


def variants(base, episode, config):
    start, end = coordinates(base, episode)
    cuts, pruned = [base], []
    seen = {(start, end)}
    for edge in ['start', 'end']:
        t = start if edge == 'start' else end
        boundaries = sorted((b for b in episode.boundaries.values() if b['safe'] and any(
            o['edge'] == edge and o['kind'] == 'dialogue' for o in b['origins'])), key=lambda b: b['ms'])
        for direction in ['previous', 'next']:
            neighbors = [b for b in boundaries if 0 < (t-b['ms'] if direction == 'previous' else b['ms']-t) <= config['neighbor_radius_ms']]
            if not neighbors:
                pruned.append({'variant': f'{direction}_{edge}', 'reason': 'no_safe_neighbor'})
                continue
            b = min(neighbors, key=lambda b: abs(b['ms']-t))
            cut = candidate(episode, b['boundary_id'] if edge == 'start' else base.start_boundary_id,
                            b['boundary_id'] if edge == 'end' else base.end_boundary_id,
                            base.family_id, f'{direction}_{edge}')
            interval = coordinates(cut, episode)
            errors = validate_cut(cut, episode, config)
            if errors or interval in seen or len(cuts) >= config['max_variants']:
                pruned.append({'variant': cut.variant, 'reason': errors or ['duplicate_or_variant_budget']})
            else:
                cuts.append(cut)
                seen.add(interval)
    return cuts, pruned
