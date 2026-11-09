from .schemas import Candidate, digest


def register(episode):
    boundaries = {}
    for block in episode.blocks:
        for edge, t in [('start', block.start_ms), ('end', block.end_ms)]:
            key = f'{episode.episode_id}:t{t:09d}'
            b = boundaries.setdefault(key, {'boundary_id': key, 'ms': t, 'origins': [], 'safe': True})
            b['origins'].append({'block_id': block.block_id, 'edge': edge, 'kind': block.kind})
    speech = [b for b in episode.blocks if b.kind == 'dialogue']
    for b in boundaries.values():
        b['safe'] = not any(x.start_ms < b['ms'] < x.end_ms for x in speech)
    episode.boundaries = boundaries
    return boundaries


def candidate(episode, start, end, family, variant='base'):
    return Candidate('cut-'+digest([episode.source_sha256, start, end])[:20], family,
                     episode.episode_id, start, end, variant)


def coordinates(cut, episode):
    try:
        return episode.boundaries[cut.start_boundary_id]['ms'], episode.boundaries[cut.end_boundary_id]['ms']
    except KeyError as exc:
        raise ValueError(f'Unknown source boundary: {exc}') from exc


def validate_cut(cut, episode, config):
    errors = []
    if cut.episode_id != episode.episode_id:
        errors.append('episode_mismatch')
    try:
        start, end = coordinates(cut, episode)
    except ValueError as exc:
        return [str(exc)]
    if not 0 <= start < end <= episode.extent_ms:
        errors.append('source_bounds')
    if not config['min_duration_ms'] <= end-start <= config['max_duration_ms']:
        errors.append('duration')
    if not all(episode.boundaries[k]['safe'] for k in [cut.start_boundary_id, cut.end_boundary_id]):
        errors.append('speech_split')
    return errors


def included(cut, episode, kind=None):
    start, end = coordinates(cut, episode)
    return sorted([b for b in episode.blocks if b.start_ms < end and b.end_ms > start and (kind is None or b.kind == kind)],
                  key=lambda b: (b.start_ms, b.ordinal))


def valid_windows(episode, config, dialogue_only=False):
    values = list(episode.boundaries.values())
    def eligible(b, edge):
        return b['safe'] and any(o['edge'] == edge and (not dialogue_only or o['kind'] == 'dialogue') for o in b['origins'])
    starts = sorted((b for b in values if eligible(b, 'start')), key=lambda b: b['ms'])
    ends = sorted((b for b in values if eligible(b, 'end')), key=lambda b: b['ms'])
    for s in starts:
        for e in ends:
            duration = e['ms']-s['ms']
            if config['min_duration_ms'] <= duration <= config['max_duration_ms']:
                yield s['boundary_id'], e['boundary_id']
