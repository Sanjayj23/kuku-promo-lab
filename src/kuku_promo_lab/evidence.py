from .timeline import coordinates


def resolve_block_ids(ids, cut, episode, opener=False, hook_ms=3000):
    if not ids:
        raise ValueError('Evidence must be nonempty')
    blocks = {b.block_id: b for b in episode.blocks}
    start, end = coordinates(cut, episode)
    for bid in ids:
        if bid not in blocks:
            raise ValueError(f'Unknown block evidence {bid}')
        b = blocks[bid]
        if not start <= b.start_ms < b.end_ms <= end:
            raise ValueError(f'Out-of-cut evidence {bid}')
        if opener and (b.kind != 'dialogue' or b.end_ms > start+hook_ms):
            raise ValueError(f'Hook evidence not fully available within first {hook_ms} ms: {bid}')
    return [blocks[bid] for bid in ids]


def source_ref(block, episode):
    return {'evidence_id': block.block_id, 'source_episode': episode.episode_id, 'source_sha256': episode.source_sha256,
            'block_id': block.block_id, 'line_start': block.line_start, 'line_end': block.line_end}
