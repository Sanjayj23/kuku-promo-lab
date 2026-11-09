"""V4 evidence contracts. Provenance checks do not establish semantic entailment."""
from copy import deepcopy
import re

from .editorial import OPENER_SCHEMA, validate_opener
from .grounding import content_tokens, grounding_flags, quote_in_words, token_found, words
from .ranking import eligibility
from .reviewer import review_schema, validate_review
from .schemas import STR, obj

SHORT = {'type': 'string', 'maxLength': 140}
NOTE = {'type': 'string', 'minLength': 1, 'maxLength': 120}
NOTES = {'type': 'array', 'items': NOTE, 'maxItems': 4, 'uniqueItems': True}
REF = obj({'utterance_id': STR, 'quote': dict(STR, maxLength=140)})
REFS = {'type': 'array', 'items': REF, 'maxItems': 3, 'uniqueItems': True}
ROLE = obj({'label': SHORT, 'voice': {'type': ['string', 'null']},
            'support': {'enum': ['supported', 'uncertain', 'not_applicable']},
            'basis': {'enum': ['self_statement', 'explicit_address', 'mentioned', 'antecedent', 'unknown']},
            'evidence': REFS})
ENTITY = obj({'entity': dict(STR, maxLength=80), 'source_mention': dict(SHORT, maxLength=80),
              'source_quote': SHORT, 'utterance_id': {'type': ['string', 'null']},
              'kind': {'enum': ['person', 'physical_object', 'kinship', 'relationship', 'event', 'concept']},
              'support': {'enum': ['supported', 'uncertain', 'unsupported']},
              'question_terms': {'type': 'array', 'items': dict(STR, maxLength=40), 'maxItems': 8, 'uniqueItems': True}})
CHAIN = obj({'antecedent': REF, 'referring_expression': REF, 'meaning': NOTE,
             'resolution': {'enum': ['supported', 'uncertain']}})
EVIDENCE_MODEL = obj({'proposition': SHORT, 'actor': ROLE, 'target': ROLE,
    'entities': {'type': 'array', 'items': ENTITY, 'maxItems': 4},
    'reference_chains': {'type': 'array', 'items': CHAIN, 'maxItems': 2},
    'opener_evidence': REFS, 'ending_evidence': REFS, 'local_stakes': REFS, 'uncertainty': NOTES})


def restricted_packet(packet):
    allowed = {'rubric_version', 'duration_ms', 'hook_window_ms', 'utterances', 'opener_ids', 'candidate_token'}
    utterance_keys = {'id', 'voice', 'delivery', 'text', 'start_ms', 'end_ms',
                      'complete_in_opener', 'crosses_opener_unknown_prefix'}
    if set(packet)-allowed or any(set(u)-utterance_keys for u in packet['utterances']):
        raise ValueError('V4 restricted packet contains non-allowlisted context')
    return packet


def bind_source_ids(schema, packet):
    schema = deepcopy(schema)
    ids = [u['id'] for u in packet['utterances']]
    voices = sorted({u['voice'] for u in packet['utterances']})
    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'properties':
                    if 'utterance_id' in child:
                        nullable = isinstance(child['utterance_id'].get('type'),list) and 'null' in child['utterance_id']['type']
                        child['utterance_id'] = {'type':['string','null'],'enum':[None]+ids} if nullable else dict(STR, enum=ids)
                    if 'voice' in child:
                        child['voice'] = {'type': ['string', 'null'], 'enum': [None]+voices}
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(schema)
    return schema


def opener_payload(packet):
    # No full-cut duration, later lines, analyst claims, source IDs or metadata.
    return {'duration_ms': 3000, 'utterances': [
        {k: u[k] for k in ['id', 'voice', 'start_ms', 'end_ms', 'text']}
        for u in packet['utterances'] if u['id'] in packet['opener_ids']]}


def opener_schema(packet):
    schema = deepcopy(OPENER_SCHEMA)
    schema['properties'].update({'standalone': {'enum': ['supported', 'uncertain', 'absent']},
        'evidence_ids': {'type': 'array', 'items': {'enum': packet['opener_ids']} if packet['opener_ids'] else STR,
                         'maxItems': 3, 'uniqueItems': True}})
    schema['required'] += ['standalone', 'evidence_ids']
    return schema


def validate_v4_opener(value, packet):
    validate_opener(value, opener_payload(packet))
    cited = [u for u in packet['utterances'] if u['id'] in value['evidence_ids']]
    if not set(value['evidence_ids']) <= set(packet['opener_ids']):
        raise ValueError('V4 opener evidence outside complete three-second speech')
    if value['hook_present'] and (not cited or not any(quote_in_words(value['cue_quote'], u['text']) for u in cited)):
        raise ValueError('V4 opener cue must be word-aligned in its cited opener')
    if value['hook_present'] and (value['uncertain'] or value['standalone'] != 'supported'):
        raise ValueError('V4 positive opener conflicts with its uncertainty')


def validate_refs(refs, packet):
    by_id = {u['id']: u for u in packet['utterances']}
    for ref in refs:
        u = by_id.get(ref['utterance_id'])
        if not u or ref['quote'] not in u['text'] or not quote_in_words(ref['quote'], u['text']):
            raise ValueError('V4 quote must be verbatim and word-aligned in the cited utterance')


def validate_evidence(model, packet):
    by_id = {u['id']: u for u in packet['utterances']}
    voices = {u['voice'] for u in by_id.values()}
    for role_name in ['actor', 'target']:
        role = model[role_name]
        validate_refs(role['evidence'], packet)
        if role['voice'] is not None and role['voice'] not in voices:
            raise ValueError('V4 invented local participant voice')
        if role['support'] == 'supported' and (not role['label'].strip() or not role['evidence']):
            raise ValueError('V4 supported participant needs source evidence')
        if role['support'] == 'supported' and role['basis'] == 'unknown':
            raise ValueError('V4 supported participant cannot have unknown attribution basis')
    for entity in model['entities']:
        if entity['utterance_id'] is None:
            if entity['support'] == 'supported' or entity['source_quote'] or entity['source_mention']:
                raise ValueError('V4 absent entity evidence cannot substantiate a supported entity')
            continue
        if not entity['source_quote'] or not entity['source_mention']:
            raise ValueError('V4 cited entity needs an actual quotation and mention')
        validate_refs([{'utterance_id': entity['utterance_id'], 'quote': entity['source_quote']}], packet)
        if not quote_in_words(entity['source_mention'], entity['source_quote']):
            raise ValueError('V4 source mention must appear in the supporting quote')
    for chain in model['reference_chains']:
        validate_refs([chain['antecedent'], chain['referring_expression']], packet)
        if by_id[chain['antecedent']['utterance_id']]['start_ms'] > by_id[chain['referring_expression']['utterance_id']]['start_ms']:
            raise ValueError('V4 antecedent cannot occur after the referring expression')
    for name in ['opener_evidence', 'ending_evidence', 'local_stakes']:
        validate_refs(model[name], packet)
    if any(ref['utterance_id'] not in packet['opener_ids'] for ref in model['opener_evidence']):
        raise ValueError('V4 opener claim references speech outside three seconds')


def full_review_schema(packet):
    schema = review_schema(packet)
    schema['properties']['evidence_model'] = deepcopy(EVIDENCE_MODEL)
    schema['required'].append('evidence_model')
    question = schema['properties']['unresolved_question']['properties']
    question['text'] = {'type': 'string', 'maxLength': 140}
    question['evidence_ids']['minItems'] = 0
    return bind_source_ids(schema, packet)


def validate_full_review(value, packet):
    validate_review(value, packet)
    if not value['interpretation']['evidence_ids']:
        raise ValueError('V4 interpretation needs contained source evidence')
    validate_evidence(value['evidence_model'], packet)
    if value['unresolved_question']['text'].strip() and not value['evidence_model']['proposition'].strip():
        raise ValueError('V4 question requires an explicit atomic proposition')
    if value['unresolved_question']['text'].strip() and not value['unresolved_question']['evidence_ids']:
        raise ValueError('V4 proposed question needs contained source evidence')
    if not value['unresolved_question']['text'].strip() and value['evidence_model']['proposition'].strip():
        raise ValueError('V4 cannot retain a proposition after abstaining from a question')


def ending_schema(packet, question, proposition):
    schema = obj({'question': {'const': question}, 'proposed_proposition': {'const': proposition},
        'status': {'enum': ['unanswered', 'answered', 'contradicted', 'disputed', 'unknown']},
        'answer_kind': {'enum': ['acceptance', 'refusal', 'assertion', 'denial', 'completed_action',
                                 'request', 'offer', 'threat', 'question', 'none', 'uncertain']},
        'answer_quote': SHORT, 'answer_evidence': REFS, 'ending_evidence': REFS,
        'context': SHORT, 'unresolved_consequence': SHORT,
        'evidence_model': deepcopy(EVIDENCE_MODEL), 'uncertainty': NOTES})
    schema['properties']['evidence_model']['properties']['proposition'] = {'const': proposition}
    return bind_source_ids(schema, packet)


def validate_v4_ending(value, packet, question, proposition):
    if value['question'] != question or value['proposed_proposition'] != proposition:
        raise ValueError('V4 answer checker changed the fixed question/proposition')
    if value['evidence_model']['proposition'] != proposition:
        raise ValueError('V4 evidence model changed the atomic proposition')
    validate_evidence(value['evidence_model'], packet)
    validate_refs(value['answer_evidence'], packet)
    validate_refs(value['ending_evidence'], packet)
    if value['status'] in {'answered', 'contradicted', 'disputed'}:
        if not value['answer_quote'] or not any(value['answer_quote'] in ref['quote'] for ref in value['answer_evidence']):
            raise ValueError('V4 resolved/disputed proposition needs a cited answer quote')
        if value['answer_kind'] in {'request', 'offer', 'threat', 'question', 'none', 'uncertain'}:
            raise ValueError('V4 offer/request/threat/question is not evidence of a resolved proposition')
    if value['status'] == 'unanswered' and (not value['ending_evidence'] or not value['context'].strip()
                                          or not value['unresolved_consequence'].strip()):
        raise ValueError('V4 unanswered verdict requires closing evidence, context and consequence')
    if value['status'] == 'unknown' and not value['uncertainty']:
        raise ValueError('V4 unknown verdict needs explicit remaining uncertainty')


def check_full(packet, client):
    restricted_packet(packet)
    return client.generate('v4/reviewer', packet, full_review_schema(packet),
                           lambda v: validate_full_review(v, packet))


def check_opener(packet, client):
    restricted_packet(packet)
    if not packet['opener_ids']:
        return None
    return client.generate('v4/opener_review', opener_payload(packet), opener_schema(packet),
                           lambda v: validate_v4_opener(v, packet))


def check_ending(packet, client, question, proposition):
    restricted_packet(packet)
    payload = dict(packet, proposed_question=question, proposed_proposition=proposition)
    return client.generate('v4/answer_check', payload, ending_schema(packet, question, proposition),
                           lambda v: validate_v4_ending(v, packet, question, proposition))


def ending_view(value):
    """Legacy-compatible export view, always recomputed from the saved V4 record."""
    if value is None:
        return None
    model = value['evidence_model']
    refs = value['ending_evidence']
    return {'question': value['question'], 'status': value['status'],
        'actor_voice': model['actor']['voice'], 'target_voice': model['target']['voice'],
        'ending_evidence_ids': list(dict.fromkeys(ref['utterance_id'] for ref in refs)),
        'ending_quote': refs[-1]['quote'] if refs else '', 'answer_quote': value['answer_quote'],
        'context': value['context'], 'unresolved_consequence': value['unresolved_consequence'],
        'entities': [{k: entity[k] for k in ['entity', 'source_quote', 'utterance_id']} for entity in model['entities']],
        'supporting_evidence_ids': list(dict.fromkeys(ref['utterance_id'] for role in ['actor', 'target'] for ref in model[role]['evidence'])),
        'uncertainty_flags': [], 'uncertainty_reason': '; '.join(value['uncertainty'])}


def binding_flags(model, packet):
    """Checks of evidence structure; these flags cannot certify a model's meaning."""
    flags, details = set(), {}
    by_id = {u['id']: u for u in packet['utterances']}
    for name in ['actor', 'target']:
        role = model[name]
        if re.fullmatch(r'voice_\d+', role['label']) and role['voice'] != role['label']:
            flags.add('participant_label_voice_mismatch')
        if role['support'] == 'uncertain' or (role['support'] == 'not_applicable' and name == 'actor'):
            flags.add('participant_uncertain')
        if role['support'] == 'supported' and role['voice'] is not None:
            if role['basis'] == 'self_statement' and any(by_id[r['utterance_id']]['voice'] != role['voice'] for r in role['evidence']):
                flags.add('participant_binding_mismatch')
            if role['basis'] == 'mentioned':
                flags.add('participant_voice_not_established')
            if role['basis'] == 'explicit_address' and role['evidence'] and all(
                    by_id[r['utterance_id']]['voice'] == role['voice'] for r in role['evidence']):
                flags.add('speaker_addressee_binding_mismatch')
        if role['basis'] == 'antecedent' and not model['reference_chains']:
            flags.add('participant_reference_chain_missing')
    for entity in model['entities']:
        if entity['support'] != 'supported':
            flags.add('entity_uncertain_or_unsupported')
        local_person = entity['kind'] == 'person' and any(
            role['voice'] == entity['entity'] and role['support'] == 'supported'
            and role['basis'] == 'self_statement'
            and any(r['utterance_id'] == entity['utterance_id'] for r in role['evidence'])
            and entity['utterance_id'] in by_id and by_id[entity['utterance_id']]['voice'] == entity['entity']
            for role in [model['actor'],model['target']])
        if entity['kind'] in {'person', 'physical_object', 'kinship'} and not local_person and not quote_in_words(entity['entity'], entity['source_quote']):
            flags.add('entity_mention_not_grounded')
            details.setdefault('entity_mention_not_grounded', []).append(entity['entity'])
    if any(chain['resolution'] != 'supported' for chain in model['reference_chains']):
        flags.add('reference_chain_uncertain')
    if model['uncertainty']:
        flags.add('semantic_uncertainty')
    return flags, details


def editorial_result(packet, full, opener, ending, visual=False, ambiguous=False, minimum_specificity=1):
    hard, flags, audit = set(), set(), {}
    if full is None:
        return None, 'B', ['full_review_unavailable'], ['full_review_unavailable']
    result = deepcopy(full)
    opener_ids = [u['id'] for u in packet['utterances'] if u['id'] in packet['opener_ids'] and opener
                  and u['id'] in opener['evidence_ids'] and quote_in_words(opener['cue_quote'], u['text'])]
    hook_ok = bool(opener and opener['hook_present'] and not opener['uncertain'] and
                   opener['standalone'] == 'supported' and opener_ids and opener['specificity'] >= minimum_specificity)
    if not hook_ok:
        (flags if visual else hard).add('visual_hook_unverified' if visual else 'no_specific_dialogue_hook')
    result['criteria']['hook'] = {'score': 1+opener['specificity'] if hook_ok else None,
        'evidence_ids': opener_ids if hook_ok else [], 'reason': opener['reason'] if opener else 'No complete three-second cue.'}
    flags.update(full['uncertainty_flags'])
    for model in [full['evidence_model']]+([ending['evidence_model']] if ending else []):
        extra, details = binding_flags(model, packet)
        flags.update(extra); audit.update(details)
        if not model['local_stakes']:
            flags.add('stakes_evidence_missing')
    if ambiguous:
        flags.add('ambiguous_tag')
    remaining = [e for e in eligibility(result) if not e.startswith('hook_')]
    hard.update(e for e in remaining if '_below_' in e or e == 'essential_context_missing')
    flags.update(set(remaining)-hard)
    view = ending_view(ending)
    if ending is None:
        flags.add('ending_unknown')
    else:
        if ending['status'] in {'answered', 'contradicted'}:
            hard.add('question_'+ending['status'])
        elif ending['status'] != 'unanswered':
            flags.add('question_'+ending['status'])
        if ending['uncertainty']:
            flags.add('semantic_uncertainty')
        if view['question'] != full['unresolved_question']['text'] or ending['proposed_proposition'] != full['evidence_model']['proposition']:
            hard.add('fixed_question_mismatch')
        ending_refs = ending['ending_evidence']
        if not ending_refs or max(next(u['end_ms'] for u in packet['utterances'] if u['id'] == ref['utterance_id']) for ref in ending_refs) < packet['duration_ms']-8000:
            flags.add('ending_evidence_not_local')
        # Differences between independent same-cut participant assignments remain visible.
        for role in ['actor', 'target']:
            a, b = full['evidence_model'][role], ending['evidence_model'][role]
            if a['support'] == b['support'] == 'supported' and a['voice'] != b['voice']:
                flags.add('independent_participant_disagreement')
    legacy_flags, legacy_details = grounding_flags(packet, full, opener, view, opener_ids)
    flags.update(legacy_flags)
    resolved = []
    # Never clear literal missing people/objects/kinship through a shared token.
    # Explicit abstract/inflection evidence can resolve a lexical alert, as a model
    # judgment recorded in the audit, not deterministic proof of the paraphrase.
    if ending:
        entities = ending['evidence_model']['entities']
        full_entities = full['evidence_model']['entities']
        def corroborated(entity):
            return any(other['support'] == 'supported' and
                other['kind'] in {'relationship', 'event', 'concept'} and
                content_tokens(other['source_mention']) == content_tokens(entity['source_mention']) and
                set(other['question_terms']) & set(entity['question_terms'])
                for other in full_entities)
        missing = legacy_details.get('question_terms_unverified', [])
        allowed = {term for entity in entities if entity['support'] == 'supported'
                   and entity['kind'] in {'relationship', 'event', 'concept'} and corroborated(entity)
                   for text in entity['question_terms'] for term in content_tokens(text)}
        unhandled = [term for term in missing if term not in allowed]
        if missing and not unhandled:
            flags.discard('question_terms_unverified')
            resolved.append({'flag': 'question_terms_unverified', 'terms': missing,
                             'basis': 'two_role_semantic_paraphrase_evidence_not_proof'})
        bad = legacy_details.get('entity_not_in_quote', [])
        by_name = {e['entity']: e for e in entities}
        if bad and all(name in by_name and by_name[name]['kind'] in {'relationship', 'event', 'concept'}
                       and by_name[name]['support'] == 'supported' and corroborated(by_name[name]) for name in bad):
            flags.discard('entity_not_in_quote')
            resolved.append({'flag': 'entity_not_in_quote', 'entities': bad,
                             'basis': 'two_role_semantic_paraphrase_evidence_not_proof'})
    audit.update({'legacy_flags': sorted(legacy_flags), 'legacy_details': legacy_details,
                  'resolved_flags': resolved, 'evidence_entailment': 'model_judgment_not_verified_by_quotes'})
    result['grounding'] = audit
    result['uncertainty_flags'] = sorted(flags | hard)
    tier = 'Excluded' if hard else 'B' if flags else 'A'
    return result, tier, sorted(flags | hard), sorted(hard)+sorted(flags)
