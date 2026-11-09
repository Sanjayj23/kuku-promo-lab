def story_index(episodes):
    return [{'episode_id': e.episode_id, 'metadata': e.metadata, 'context_basis': 'source_metadata'} for e in episodes]


def nearby_context(episode, index):
    position = next(i for i, e in enumerate(index) if e['episode_id'] == episode.episode_id)
    return index[max(0, position-1):position+2]
