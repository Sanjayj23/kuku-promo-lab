"""Opt-in scheduling and saved evidence for the bounded V4 development path."""
from .discovery_v4 import (boundary_options, deduplicate_events, extra_review_reason,
                           select_bases)
from .editorial_v4 import (check_ending, check_full, check_opener, editorial_result,
                           ending_view)
from .projection import project
from .ranking import compare_family, score
from .schemas import plain
from .timeline import coordinates, included, validate_cut


def review_cut(cut, episode, config, client, failures, progress=None):
    errors = validate_cut(cut, episode, config)
    if errors:
        raise ValueError(f'V4 invalid candidate before inference: {errors}')
    start, end = coordinates(cut, episode)
    packet, mapping = project(cut, episode, config['hook_window_ms'])
    row = {'candidate': plain(cut), 'start_ms': start, 'end_ms': end,
           'duration_ms': end-start, 'packet': packet, 'mapping': mapping,
           'dialogue': '\n'.join(u['text'] for u in packet['utterances']),
           'full_review': None, 'opener': None, 'semantic_ending': None,
           'semantic_contract_version': 4}
    if progress:
        progress(f'V4 reviewing {cut.episode_id} / {cut.variant}')
    for field, check in [('full_review', check_full), ('opener', check_opener), ('semantic_ending', check_ending)]:
        if not packet['utterances']:
            continue
        try:
            if field == 'semantic_ending':
                if row['full_review'] is None:
                    continue
                full = row['full_review']
                if not full['unresolved_question']['text'].strip() or not full['evidence_model']['proposition'].strip():
                    row['ending_not_requested'] = 'no_supported_forward_question'
                    continue
                row[field] = check(packet, client, full['unresolved_question']['text'], full['evidence_model']['proposition'])
            else:
                row[field] = check(packet, client)
        except (ValueError, RuntimeError, OSError) as exc:
            failures.append({'stage': 'v4_'+field, 'candidate_id': cut.candidate_id, 'error': str(exc)})
    blocks = included(cut, episode)
    row['ending'] = ending_view(row['semantic_ending'])
    row['review'], row['tier'], row['flags'], row['eligibility_errors'] = editorial_result(
        packet, row['full_review'], row['opener'], row['semantic_ending'],
        any(b.kind == 'action' and b.start_ms < start+3000 for b in blocks),
        any(b.kind == 'ambiguous_tag' for b in blocks), config['minimum_hook_specificity'])
    return row


def search_reviews(bases, episodes, config, client, failures, progress=None):
    kept, duplicates = deduplicate_events(bases, episodes)
    selected, pruned, selection_log = select_bases(kept, episodes, config['v4_initial_candidates'])
    families, rejections, extra_log = [], duplicates+pruned, []
    for base, proposal in selected:
        row = review_cut(base, episodes[base.episode_id], config, client, failures, progress)
        families.append({'family_id': base.family_id, 'episode_id': base.episode_id,
                         'proposal': proposal, 'variants': [row], 'pruned': []})
    remaining, compared_families = config['v4_extra_reviews'], 0
    ranked = sorted(zip(selected, families), key=lambda pair: (
        -(score(pair[1]['variants'][0]['full_review']['criteria']) or 0) if pair[1]['variants'][0]['full_review'] else 0,
        pair[0][0].candidate_id))
    for (base, _), family in ranked:
        reason = extra_review_reason(family['variants'][0])
        if not reason or remaining <= 0 or compared_families >= config['v4_extra_families']:
            continue
        options, discarded = boundary_options(base, episodes[base.episode_id], config)
        family['pruned'] += discarded
        if not options:
            continue
        compared_families += 1
        for cut in options[:remaining]:
            family['variants'].append(review_cut(cut, episodes[cut.episode_id], config, client, failures, progress))
            remaining -= 1
            extra_log.append({'candidate_id': cut.candidate_id, 'family_id': base.family_id,
                              'variant': cut.variant, 'allocation_reason': reason})
    winners = []
    for family in families:
        winner, family['comparison'] = compare_family(family['variants'])
        if winner:
            winners.append(winner)
        for row in family['variants']:
            if row['eligibility_errors']:
                rejections.append({'candidate_id': row['candidate']['candidate_id'], 'family_id': family['family_id'],
                                   'reason': row['eligibility_errors']})
    audit = {'version': 4, 'raw_proposals': len(bases), 'provisional_event_groups': len(kept),
             'event_group_status': 'structural_overlap_deduplication_not_human_verified',
             'duplicate_proposals': duplicates, 'initial_reviews': len(selected),
             'extra_reviews': len(extra_log), 'families_with_extra_reviews': compared_families,
             'selection': selection_log, 'extra_allocation': extra_log,
             'source_valid_review_visits': sum(len(f['variants']) for f in families),
             'human_editorial_ratings': 'pending', 'true_unseen_story_evaluation': 'pending'}
    return kept, families, winners, rejections, audit
