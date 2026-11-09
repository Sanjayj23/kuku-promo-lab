"""Bounded structural discovery. Event groups are provisional, not human truth."""
from collections import Counter

from .ranking import text_similarity
from .search import provisional_score, variants
from .timeline import candidate, coordinates, included, valid_windows, validate_cut


def complete_opener(start, episode):
    return any(b.kind == 'dialogue' and start <= b.start_ms < b.end_ms <= start+3000
               for b in episode.blocks)


def menu(episode, config, original):
    """Retain every original feasible pair; refill free slots without a quality score."""
    possible = list(valid_windows(episode, config, dialogue_only=True))
    feasible = {s for s, _ in possible if complete_opener(episode.boundaries[s]['ms'], episode)}
    retained = [p for p in original if p[0] in feasible]
    unknown = [{'start_boundary_id': s, 'end_boundary_id': e,
                'reason': 'unknown_three_second_prefix', 'route': 'visual_or_human_review'}
               for s, e in original if s not in feasible]
    groups = {}
    for pair in possible:
        if pair[0] in feasible:
            groups.setdefault(pair[0], []).append(pair)
    anchors = sorted(groups, key=lambda key: episode.boundaries[key]['ms'])
    for key in anchors:
        groups[key].sort(key=lambda p: (abs(episode.boundaries[p[1]]['ms']-
                                           episode.boundaries[p[0]]['ms']-40000), p))
    ordered = [groups[key][depth] for depth in range(max(map(len, groups.values()), default=0))
               for key in anchors if depth < len(groups[key])]
    order = {pair: i for i, pair in enumerate(ordered)}
    result = list(retained)
    while len(result) < config['max_base_windows']:
        remaining = [p for p in ordered if p not in result]
        if not remaining:
            break
        starts, ends = {p[0] for p in result}, {p[1] for p in result}
        chosen = min(remaining, key=lambda p: (-(int(p[0] not in starts)+int(p[1] not in ends)), order[p]))
        result.append(chosen)
    chosen = original if config.get('v4_menu_policy', 'retain_feasible_refill') == 'original' else result
    return chosen, {'episode_id': episode.episode_id, 'policy': config.get('v4_menu_policy'),
                    'original_pairs': len(original), 'retained_feasible_pairs': len(retained),
                    'selected_pairs': len(chosen), 'refilled_pairs': len(set(chosen)-set(original)),
                    'retained_pairs': [list(p) for p in retained], 'unknown_openings': unknown,
                    'quality_verified': False}


def deduplicate_events(bases, episodes):
    kept, rejected = [], []
    for cut, proposal in sorted(bases, key=lambda item: (-provisional_score(item[1]), item[0].candidate_id)):
        ep = episodes[cut.episode_id]
        start, end = coordinates(cut, ep)
        anchors = set(proposal.get('event_anchor_ids', []))
        speech = '\n'.join(b.text for b in included(cut, ep, 'dialogue'))
        duplicate = None
        for prior, previous in kept:
            if prior.episode_id != cut.episode_id:
                continue
            ps, pe = coordinates(prior, ep)
            iou = max(0, min(end, pe)-max(start, ps))/(max(end, pe)-min(start, ps))
            pa = set(previous.get('event_anchor_ids', []))
            anchor_overlap = len(anchors & pa)/min(len(anchors), len(pa)) if anchors and pa else 0
            prior_speech = '\n'.join(b.text for b in included(prior, ep, 'dialogue'))
            dialogue_overlap = text_similarity(speech, prior_speech)
            if cut.family_id == prior.family_id or cut.candidate_id == prior.candidate_id or iou >= .7 or dialogue_overlap >= .8 or (anchor_overlap >= .5 and iou >= .5):
                duplicate = {'reason': 'overlapping_event_group', 'winner_id': prior.candidate_id,
                             'interval_iou': iou, 'dialogue_similarity': dialogue_overlap,
                             'anchor_overlap': anchor_overlap}
                break
        if duplicate:
            rejected.append(dict(duplicate, candidate_id=cut.candidate_id, family_id=cut.family_id,
                                 episode_id=cut.episode_id, event_summary=proposal['event_summary'],
                                 classification='provisional_duplicate_not_semantic_proof'))
        else:
            kept.append((cut, proposal))
    return kept, rejected


def select_bases(bases, episodes, limit):
    """One event per episode before seconds, then score and source diversity in ties."""
    pending, chosen, log = list(bases), [], []
    ep_counts, scene_counts, type_counts, end_counts = Counter(), Counter(), Counter(), Counter()
    def diversity(item):
        c, p = item
        ep = episodes[c.episode_id]
        s, e = coordinates(c, ep)
        scene = next((b.scene_id for b in included(c, ep, 'dialogue')), '')
        event_type = p.get('event_type', 'other')
        end_bin = (c.episode_id, e//10000)
        return (ep_counts[c.episode_id], -provisional_score(p), scene_counts[(c.episode_id, scene)],
                type_counts[event_type], end_counts[end_bin], c.candidate_id)
    while pending and len(chosen) < limit:
        item = min(pending, key=diversity)
        c, p = item
        priority = diversity(item)
        pending.remove(item); chosen.append(item)
        scene = next((b.scene_id for b in included(c, episodes[c.episode_id], 'dialogue')), '')
        end = coordinates(c, episodes[c.episode_id])[1]
        log.append({'candidate_id': c.candidate_id, 'episode_id': c.episode_id,
                    'event_summary': p['event_summary'], 'slot': len(chosen),
                    'priority': list(priority), 'reason': 'episode_first_then_score_then_scene_event_type_ending_diversity',
                    'event_group_status': 'provisional_not_human_verified'})
        ep_counts[c.episode_id] += 1
        scene_counts[(c.episode_id, scene)] += 1
        type_counts[p.get('event_type', 'other')] += 1
        end_counts[(c.episode_id, end//10000)] += 1
    pruned = [{'family_id': c.family_id, 'candidate_id': c.candidate_id, 'reason': 'initial_review_budget'} for c, _ in pending]
    return chosen, pruned, log


def boundary_options(base, episode, config):
    if config['max_variants'] <= 1:
        return [], []
    singles, pruned = variants(base, episode, dict(config, max_variants=5))
    start, end = coordinates(base, episode)
    joint = []
    if config.get('v4_combined_boundary', 1):
        starts = {c.start_boundary_id for c in singles if c.start_boundary_id != base.start_boundary_id}
        ends = {c.end_boundary_id for c in singles if c.end_boundary_id != base.end_boundary_id}
        # Neighbors pruned as single-edge duration violations can still form a valid joint cut.
        for edge, anchor, pool in [('start', start, starts), ('end', end, ends)]:
            for direction in [-1, 1]:
                candidates = [b for b in episode.boundaries.values() if b['safe'] and
                    0 < direction*(b['ms']-anchor) <= config['neighbor_radius_ms'] and
                    any(o['edge'] == edge and o['kind'] == 'dialogue' for o in b['origins'])]
                if candidates:
                    pool.add(min(candidates, key=lambda b: abs(b['ms']-anchor))['boundary_id'])
        for s in sorted(starts):
            for e in sorted(ends):
                cut = candidate(episode, s, e, base.family_id, 'combined_start_end')
                errors = validate_cut(cut, episode, config)
                if errors:
                    pruned.append({'variant': 'combined_start_end', 'start_boundary_id': s,
                                   'end_boundary_id': e, 'reason': errors})
                else:
                    joint.append(cut)
    def structural_order(c):
        s, e = coordinates(c, episode)
        return (not complete_opener(s, episode), abs(s-start)+abs(e-end), e-s, c.candidate_id)
    joint.sort(key=structural_order)
    extras = sorted(singles[1:], key=structural_order)
    if joint:
        # Exactly one joint candidate, inside the three-extra/four-total cap.
        extras = [joint[0]]+extras
    result, seen = [], {base.candidate_id}
    for cut in extras:
        if cut.candidate_id not in seen:
            result.append(cut); seen.add(cut.candidate_id)
        if len(result) >= min(3, config['max_variants']-1):
            break
    return result, pruned


def extra_review_reason(record):
    review = record.get('review')
    if not review:
        return None
    c = review['criteria']
    if all((c[k]['score'] or 0) >= threshold for k, threshold in
           [('comprehension', 2), ('emotion', 2), ('coherence', 2), ('cliffhanger', 2)]):
        return 'potential_local_stakes_and_ending; boundary comparison, not acceptance'
    return None
