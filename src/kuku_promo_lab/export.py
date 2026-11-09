import csv
import hashlib
import json
from pathlib import Path

from .evidence import resolve_block_ids, source_ref
from .parser import timecode
from .ranking import score
from .schemas import CRITERIA


def source_evidence_model(model, mapping):
    """Convert anonymous U references to source IDs without changing model claims."""
    if isinstance(model, list):
        return [source_evidence_model(v, mapping) for v in model]
    if isinstance(model, dict):
        return {k: mapping[v] if k == 'utterance_id' else source_evidence_model(v, mapping)
                for k, v in model.items()}
    return model


def model_evidence_ids(value):
    if isinstance(value, dict):
        return [value['utterance_id']] if 'utterance_id' in value else [i for v in value.values() for i in model_evidence_ids(v)]
    if isinstance(value, list):
        return [i for v in value for i in model_evidence_ids(v)]
    return []


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')


def write_jsonl(path, values):
    Path(path).write_text(''.join(json.dumps(x, ensure_ascii=False)+'\n' for x in values))


def csv_safe(text):
    text = str(text)
    return "'"+text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else text


def recommendation(record, rank, episode, near_tie=False, comparison=None, gate_version=2):
    c, review, mapping = record['candidate'], record['review'], record['mapping']
    def mapped(claim):
        return dict(claim, evidence_ids=list(dict.fromkeys(mapping[i] for i in claim['evidence_ids'])))
    criteria = {k: mapped(review['criteria'][k]) for k in CRITERIA}
    hook = {'text': criteria['hook']['reason'], 'evidence_ids': criteria['hook']['evidence_ids']}
    cliff = mapped(review['unresolved_question'])
    editorial = {}
    why = None
    if 'tier' in record:
        opener, ending = record['opener'], record['ending']
        hook['text'] = opener['cue_quote']
        editorial = {
            'hook_quote': opener['cue_quote'], 'ending_quote': ending['ending_quote'],
            'question_status': ending['status'], 'tier': record['tier'],
            'tie_group': f"score-{score(criteria):g}", 'source_validation_status': 'passed',
            'human_review_status': 'pending', 'human_posting_order': None,
        }
        why = (f"{ending['context'].rstrip('. ')}. The opener introduces the stakes. "
               f"The final beat leaves this question open. {ending['question']}")
    if 'tier' in record and gate_version >= 3:
        editorial['boundary_decision'] = comparison['decision']
        context = ending['context'].rstrip('. ')
        choices = [
            f'{context}. Opens on “{opener["cue_quote"]}”. Ends on “{ending["ending_quote"]}”, leaving: {ending["question"]}',
            f'Opens on “{opener["cue_quote"]}”. Ends on “{ending["ending_quote"]}”, leaving: {ending["question"]}',
            f'Opens on “{opener["cue_quote"]}”, leaving: {ending["question"]}',
        ]
        why = next(text for text in choices if len(text) <= 400)
    evidence = sorted({i for c in [hook, cliff]+list(criteria.values()) for i in c['evidence_ids']})
    if gate_version == 4:
        native = record['semantic_ending']
        model = native['evidence_model']
        editorial['semantic_evidence'] = {'contract_version': 4,
            'model': source_evidence_model(model, mapping),
            'legacy_flags': review['grounding']['legacy_flags'],
            'resolved_flags': review['grounding']['resolved_flags'],
            'entailment_status': 'model_judgment_not_human_verified'}
        evidence = sorted(set(evidence) | {mapping[i] for i in model_evidence_ids(model)})
    blocks = {b.block_id: b for b in episode.blocks}
    def phrase(text):
        return text.replace('?', '').replace('!', '').replace('.', '').strip()[:155]
    seg = {'order': 1, 'source_episode': episode.episode_id, 'source_sha256': episode.source_sha256,
           'start_boundary_id': c['start_boundary_id'], 'end_boundary_id': c['end_boundary_id'],
           'start_ms': record['start_ms'], 'end_ms': record['end_ms'], 'duration_ms': record['duration_ms'],
           'start_timecode': timecode(record['start_ms']), 'end_timecode': timecode(record['end_ms'])}
    flags = set(review['uncertainty_flags']+['script_timing_unverified_in_video', 'dialogue_only'])
    if near_tie:
        flags.add('near_tie')
    return {'promo_id': c['candidate_id'], 'rank': rank, 'family_id': c['family_id'], 'segments': [seg],
            'duration_ms': record['duration_ms'], 'hook': hook, 'cliffhanger': cliff,
            'why_it_works': why or f"Opens on {phrase(hook['text'])} and leaves {phrase(cliff['text'])}.",
            'source_evidence': [source_ref(blocks[i], episode) for i in evidence],
            'ranking': {'score': score(criteria), 'formula_version': 'editorial-v1',
                        'rationale': 'Eligible on all gates; weighted hook 20%, cliffhanger 25%, comprehension 25%, emotion/coherence/boundary 10% each; deterministic tie breaks.'},
            'quality': {'criteria': criteria, 'redundancy_score': record['redundancy_score'], 'structural_valid': True,
                        'evidence_resolved': True, 'semantic_status': 'model_checked', 'review_basis': 'dialogue_only',
                        'timing_basis': 'script', 'video_verified': False, 'human_status': 'not_reviewed', 'flags': sorted(flags)},
            **editorial}


EDITORIAL_FIELDS = ['hook_quote', 'ending_quote', 'question_status', 'tier', 'tie_group',
                    'source_validation_status', 'human_review_status', 'human_posting_order']


def promo_row(p):
    s = p['segments'][0]
    row = dict(rank=p['rank'], promo_id=p['promo_id'], source_episode=s['source_episode'],
               start_timecode=s['start_timecode'], end_timecode=s['end_timecode'], duration_ms=p['duration_ms'],
               score=p['ranking']['score'], hook=p['hook']['text'], cliffhanger=p['cliffhanger']['text'],
               why_it_works=p['why_it_works'], flags=';'.join(p['quality']['flags']))
    if 'tier' in p:
        row.update({key: '' if p[key] is None else p[key] for key in EDITORIAL_FIELDS})
    if 'boundary_decision' in p:
        row['boundary_decision'] = p['boundary_decision']['summary']
    return {k: csv_safe(v) for k, v in row.items()}


def promos_csv(path, promos, v2=False):
    fields = ['rank', 'promo_id', 'source_episode', 'start_timecode', 'end_timecode', 'duration_ms', 'score', 'hook', 'cliffhanger', 'why_it_works', 'flags']
    if v2:
        fields += EDITORIAL_FIELDS
    if any('boundary_decision' in p for p in promos):
        fields.append('boundary_decision')
    with Path(path).open('w', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        for p in promos:
            writer.writerow(promo_row(p))


def checksums(run_dir):
    root = Path(run_dir)
    entries = [f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(root).as_posix()}' for p in sorted(root.rglob('*'))
               if p.is_file() and p.name != 'checksums.sha256']
    (root/'checksums.sha256').write_text('\n'.join(entries)+'\n')
