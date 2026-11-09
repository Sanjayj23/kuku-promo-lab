"""Conservative lexical grounding flags. These checks do not verify meaning."""
import re
from .ranking import tokens as words

# Tunable vocabulary, copied from the review specification without fitting private outputs.
STOPWORDS = frozenset("""what which who whom whose when where why how will would could should can does did do has have had was were are is am be been being the and but for nor not now yet still ever again about after before with without from into onto over under this that these those they them their there here she her hers him his you your our out any anyone anything someone something everyone everything else other others happen happens happened next going really finally actually able get gets got make makes made take takes took let lets tell told know knows end decide decides decision choose chooses choice way one two first last then than just only also even very much many more most all both each own same such its it's what's who's""".split())


def norm(token):
    if token.endswith(("'s", "’s")):
        token = token[:-2]
    if len(token) > 3 and token.endswith('s') and not token.endswith('ss'):
        token = token[:-1]
    return token


def content_tokens(text):
    return {norm(token) for token in words(text)
            if len(norm(token)) >= 3 and norm(token) not in STOPWORDS
            and not re.fullmatch(r'voice_?\d+', norm(token)) and not norm(token).isnumeric()}


def token_found(token, vocab):
    return token in vocab or (len(token) >= 5 and any(
        len(value) >= 5 and value[:5] == token[:5] for value in vocab))


def quote_in_words(quote, text):
    quoted, source = words(quote), words(text)
    return bool(quoted) and any(source[i:i+len(quoted)] == quoted
                               for i in range(len(source)-len(quoted)+1))


def jaccard(a, b):
    return len(a & b)/len(a | b) if a | b else 0


def grounding_flags(packet, review, opener, ending, opener_ids):
    flags, details = set(), {}
    def mark(flag, items):
        flags.add(flag)
        details[flag] = sorted(set(items))

    matched = [u for u in packet['utterances'] if u['id'] in opener_ids]
    if matched and opener:
        if sum(len(words(u['text'])) for u in matched) < 3:
            mark('minimal_opener', [u['id'] for u in matched])
        if not any(quote_in_words(opener['cue_quote'], u['text']) for u in matched):
            mark('cue_quote_not_word_aligned', [u['id'] for u in matched])
    if ending and ending['status'] in {'unanswered', 'disputed'}:
        cited = [u for u in packet['utterances'] if u['id'] in ending['ending_evidence_ids']]
        if not cited:
            mark('ending_evidence_missing', ['ending_evidence_ids'])
        else:
            last_end = max(u['end_ms'] for u in cited)
            after = [u['id'] for u in packet['utterances'] if u['start_ms'] >= last_end]
            if after:
                mark('speech_after_cited_ending', after)
        actor, target = ending['actor_voice'], ending['target_voice']
        roles = {actor, target} - {None}
        support_ids = set(ending['ending_evidence_ids'] + ending['supporting_evidence_ids'])
        support_voices = {u['voice'] for u in packet['utterances'] if u['id'] in support_ids}
        if (actor is not None and actor == target) or (roles and not roles & support_voices):
            mark('role_evidence_mismatch', roles)
        bad_entities = [entity['entity'] for entity in ending['entities']
                        if content_tokens(entity['entity']) and not any(
                            token_found(t, content_tokens(entity['source_quote']))
                            for t in content_tokens(entity['entity']))]
        if bad_entities:
            mark('entity_not_in_quote', bad_entities)
        vocab = set().union(*(content_tokens(u['text']) for u in packet['utterances']))
        missing = [t for t in content_tokens(ending['question']) if not token_found(t, vocab)]
        if missing:
            mark('question_terms_unverified', missing)
        if review:
            original = content_tokens(review['unresolved_question']['text'])
            checked = content_tokens(ending['question'])
            if original and checked and jaccard(original, checked) < .2:
                mark('question_rewritten', ['original_question', 'checked_question'])
    return flags, details
