"""Allowlisted dialogue projection. Source mappings never cross the information barrier."""
from .schemas import digest
from .timeline import coordinates, included


def project(cut, episode, hook_window_ms=3000):
    start, end = coordinates(cut, episode)
    utterances, mapping, voices = [], {}, {}
    for b in included(cut, episode, 'dialogue'):
        if not start <= b.start_ms < b.end_ms <= end:
            raise ValueError('Cannot project clipped speech')
        voice = voices.setdefault(b.speaker, f'voice_{len(voices)+1}')
        uid = f'U{len(utterances)+1:03d}'
        utterances.append({'id': uid, 'voice': voice, 'delivery': b.delivery, 'text': b.text,
                           'start_ms': b.start_ms-start, 'end_ms': b.end_ms-start,
                           'complete_in_opener': b.end_ms-start <= hook_window_ms,
                           'crosses_opener_unknown_prefix': b.start_ms-start < hook_window_ms < b.end_ms-start})
        mapping[uid] = b.block_id
    allowed = {'rubric_version': 'editorial-v1', 'duration_ms': end-start, 'hook_window_ms': hook_window_ms,
               'utterances': utterances, 'opener_ids': [u['id'] for u in utterances if u['complete_in_opener']]}
    return dict(allowed, candidate_token='anonymous-'+digest(allowed)[:20]), mapping
