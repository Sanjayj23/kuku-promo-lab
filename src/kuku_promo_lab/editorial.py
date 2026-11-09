"""Independent model checks with deterministic evidence and eligibility gates."""
from copy import deepcopy

from .ranking import eligibility
from .schemas import IDS, STR, obj

SHORT = {'type': 'string', 'maxLength': 140}
FLAG_LIST = {'type': 'array', 'items': {'enum': ['unsupported_entity', 'uncertain_roles', 'needs_review']},
             'uniqueItems': True, 'maxItems': 3}
OPENER_SCHEMA = obj({
    'hook_present': {'type': 'boolean'},
    'cue_quote': SHORT,
    'cue_type': {'enum': ['threat', 'accusation', 'reveal', 'command', 'demand', 'question-with-stakes', 'shock', 'none']},
    'specificity': {'enum': [0, 1, 2]},
    'reason': dict(STR, maxLength=160),
    'evidence_scope': {'const': 'dialogue_only'},
    'uncertain': {'type': 'boolean'},
})
ENDING_SCHEMA = obj({
    'question': SHORT,
    'actor_voice': {'type': ['string', 'null']},
    'target_voice': {'type': ['string', 'null']},
    'ending_evidence_ids': {'type': 'array', 'items': STR, 'uniqueItems': True, 'maxItems': 3},
    'ending_quote': SHORT,
    'status': {'enum': ['unanswered', 'answered', 'disputed', 'unknown']},
    'answer_quote': SHORT,
    'unresolved_consequence': SHORT,
    'context': SHORT,
    'entities': {'type': 'array', 'maxItems': 4, 'items': obj({
        'entity': SHORT, 'source_quote': SHORT, 'utterance_id': STR,
    })},
    'supporting_evidence_ids': dict(IDS, minItems=0, maxItems=4),
    'uncertainty_flags': FLAG_LIST,
    'uncertainty_reason': SHORT,
})


def opener_payload(packet):
    # Reindex voices within the opening slice, including when an overlapping line is omitted.
    voices = {}
    return {'utterances': [
        {'voice': voices.setdefault(u['voice'], f'voice_{len(voices)+1}'),
         'start_ms': u['start_ms'], 'end_ms': u['end_ms'], 'text': u['text']}
        for u in packet['utterances'] if 0 <= u['start_ms'] < u['end_ms'] <= 3000
    ]}


def quote_matches(quote, utterances):
    return bool(quote.strip()) and any(quote in u['text'] for u in utterances)


def validate_opener(value, packet):
    if value['cue_quote'] and not quote_matches(value['cue_quote'], packet['utterances']):
        raise ValueError('Opener cue quote must appear verbatim in a complete opener utterance')
    if value['hook_present'] and (
        not value['cue_quote'].strip() or value['cue_type'] == 'none' or value['specificity'] == 0
    ):
        raise ValueError('Positive hook needs a verbatim cue, type and concrete stake')


def validate_ending(value, packet):
    by_id = {u['id']: u for u in packet['utterances']}
    ids = set(value['ending_evidence_ids'] + value['supporting_evidence_ids'])
    if not ids <= by_id.keys():
        raise ValueError('Ending check cited an invented utterance')
    voices = {u['voice'] for u in by_id.values()}
    if any(value[k] is not None and value[k] not in voices for k in ('actor_voice', 'target_voice')):
        raise ValueError('Ending check cited an invented voice')
    if value['ending_quote'] and not quote_matches(value['ending_quote'], [by_id[i] for i in value['ending_evidence_ids']]):
        raise ValueError('Ending quote must appear in cited ending evidence')
    if value['status'] in {'answered', 'disputed'} and not quote_matches(value['answer_quote'], by_id.values()):
        raise ValueError('An answer or dispute needs a verbatim answer quote')
    if value['status'] == 'unanswered' and not all(
        value[k].strip() for k in ('question', 'ending_quote', 'context', 'unresolved_consequence')
    ):
        raise ValueError('Unanswered status needs a supported question, ending quote, context and consequence')
    for entity in value['entities']:
        if entity['utterance_id'] not in by_id or not quote_matches(
            entity['source_quote'], [by_id[entity['utterance_id']]]
        ):
            raise ValueError('Question entity needs a verifiable supporting quotation')
    if (value['uncertainty_flags'] or value['status'] == 'unknown') and not value['uncertainty_reason'].strip():
        raise ValueError('Uncertainty needs an explanation')


def check_opener(packet, client):
    payload = opener_payload(packet)
    if not payload['utterances']:
        return None
    return client.generate('opener_review', payload, OPENER_SCHEMA,
                           lambda result: validate_opener(result, payload))


def check_ending(packet, client, proposed_question=None):
    if not packet['utterances']:
        return None
    payload = dict(packet, proposed_question=proposed_question)
    return client.generate('answer_check', payload, ENDING_SCHEMA,
                           lambda result: validate_ending(result, packet))


def _editorial_result_v2(packet, review, opener, ending, visual, ambiguous, minimum_specificity=1):
    hard, flags = [], set()
    result = deepcopy(review)
    opener_ids = [
        u['id'] for u in packet['utterances']
        if u['id'] in packet['opener_ids'] and opener and quote_matches(opener['cue_quote'], [u])
    ]
    hook_ok = (opener and opener['hook_present'] and not opener['uncertain'] and opener_ids
               and opener['specificity'] >= minimum_specificity)
    if not hook_ok:
        if visual:
            flags.add('visual_hook_unverified')
        if opener and not opener['uncertain'] and not visual:
            hard.append('no_specific_dialogue_hook')
        elif opener is None and not packet['opener_ids'] and not visual:
            hard.append('no_complete_dialogue_hook')
        else:
            flags.add('opener_unknown')
    if ambiguous:
        flags.add('ambiguous_tag')
    if ending is None:
        flags.add('ending_unknown')
    else:
        flags.update(ending['uncertainty_flags'])
        if ending['status'] == 'answered':
            hard.append('question_answered')
        elif ending['status'] == 'disputed':
            flags.add('question_disputed')
            if not ending['unresolved_consequence'].strip():
                flags.add('needs_review')
        elif ending['status'] == 'unknown':
            flags.add('ending_unknown')
        elif not any(
            u['id'] in ending['ending_evidence_ids']
            and u['end_ms'] >= packet['duration_ms'] - 8000
            and quote_matches(ending['ending_quote'], [u])
            for u in packet['utterances']
        ):
            flags.add('ending_evidence_not_local')
    if result is None:
        flags.add('full_review_unavailable')
    else:
        flags.update(result['uncertainty_flags'])
        result['criteria']['hook'] = {
            'score': 1 + opener['specificity'] if hook_ok else None,
            'evidence_ids': opener_ids if hook_ok else [],
            'reason': opener['reason'] if opener else 'Complete opener unavailable.',
        }
        if ending and ending['status'] == 'unanswered':
            result['unresolved_question'] = {'text': ending['question'], 'evidence_ids': ending['ending_evidence_ids']}
        remaining = [error for error in eligibility(result) if not error.startswith('hook_')]
        if remaining:
            flags.update(remaining)
        result['uncertainty_flags'] = sorted(flags | set(hard))
    tier = 'Excluded' if hard else 'B' if flags else 'A'
    return result, tier, sorted(flags | set(hard)), hard + sorted(flags)


def editorial_result(packet, review, opener, ending, visual, ambiguous,
                     minimum_specificity=1, gate_version=2):
    if gate_version == 2:
        return _editorial_result_v2(packet, review, opener, ending, visual, ambiguous, minimum_specificity)
    if gate_version != 3:
        raise ValueError('Unsupported editorial gate version')
    return _editorial_result_v3(packet, review, opener, ending, visual, ambiguous, minimum_specificity)


def _editorial_result_v3(packet, review, opener, ending, visual, ambiguous, minimum_specificity):
    result, _, all_flags, errors = _editorial_result_v2(
        packet, review, opener, ending, visual, ambiguous, minimum_specificity)
    hard_names = {'no_specific_dialogue_hook', 'no_complete_dialogue_hook', 'question_answered'}
    hard = [reason for reason in errors if reason in hard_names
            or ('_below_' in reason and not reason.startswith('hook_'))
            or reason == 'essential_context_missing']
    flags = set(all_flags) - set(hard)
    opener_ids = result['criteria']['hook']['evidence_ids'] if result else []
    extra, details = grounding_flags(packet, review, opener, ending, opener_ids)
    flags.update(extra)
    if result is not None:
        result['grounding'] = details
        result['uncertainty_flags'] = sorted(flags | set(hard))
    tier = 'Excluded' if hard else 'B' if flags else 'A'
    return result, tier, sorted(flags | set(hard)), hard + sorted(flags)


from .grounding import grounding_flags
