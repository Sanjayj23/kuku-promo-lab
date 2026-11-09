from .context import nearby_context
from .evidence import resolve_block_ids
from copy import deepcopy
from .schemas import ANALYSIS_SCHEMA, PROPOSAL, digest, obj, plain
from .timeline import candidate, validate_cut, valid_windows, coordinates


def analysis_schema(episode, config):
    """Constrain ID pairs, not invented numeric times, in the local JSON grammar."""
    v4 = config.get('discovery_version') == 4
    schema = deepcopy(ANALYSIS_SCHEMA)
    proposal_schema = deepcopy(PROPOSAL)
    if v4:
        from .schemas import STR, IDS
        proposal_schema['properties'].update({
            'event_anchor_ids': dict(IDS, maxItems=4),
            'event_type': {'enum': ['threat', 'decision', 'reveal', 'rescue', 'accusation',
                                    'confrontation', 'relationship', 'other']},
            'pending_change': dict(STR, maxLength=140)})
        proposal_schema['required'] += ['event_anchor_ids', 'event_type', 'pending_change']
    schema['properties']['proposals']['maxItems'] = config['max_proposals_per_episode']
    windows=base_windows(episode, config)
    if not windows:
        schema['properties']['proposals']['maxItems'] = 0
        return schema
    choices = []
    for start,end in windows:
        cut=candidate(episode,start,end,'schema')
        s,e=coordinates(cut,episode)
        ids=[b.block_id for b in episode.blocks if s <= b.start_ms < b.end_ms <= e]
        fields = {'start_boundary_id': {'const': start}, 'end_boundary_id': {'const': end}}
        fields.update(deepcopy({k:v for k,v in proposal_schema['properties'].items() if k not in fields}))
        for key in ['hook','cliffhanger']:
            fields[key]['properties']['evidence_ids']['items']={'enum':ids}
        if v4:
            fields['event_anchor_ids']['items'] = {'enum': ids}
        choices.append(obj(fields))
    schema['properties']['proposals']['items'] = {'oneOf': choices}
    return schema


def legacy_base_windows(episode,config):
    """Even temporal sampling is a search cap, never a semantic quality score."""
    windows=list(valid_windows(episode,config,dialogue_only=True))
    if not windows:
        windows=list(valid_windows(episode,config))
    limit=config['max_base_windows']
    if len(windows) <= limit:return windows
    return [windows[round(i*(len(windows)-1)/(limit-1))] for i in range(limit)] if limit > 1 else windows[:1]


def base_windows(episode, config):
    original = legacy_base_windows(episode, config)
    if config.get('discovery_version') == 4:
        from .discovery_v4 import menu
        return menu(episode, config, original)[0]
    return original


def analyst_payload(episode, index, config):
    def edge(t):
        key = f'{episode.episode_id}:t{t:09d}'
        return key if episode.boundaries[key]['safe'] else None
    payload = {'source_sha256': episode.source_sha256, 'episode': episode.episode_id, 'metadata_context': nearby_context(episode, index),
            'constraints': {k: config[k] for k in ['min_duration_ms', 'max_duration_ms', 'hook_window_ms', 'max_proposals_per_episode']},
            'block_columns': ['block_id', 'scene_id', 'kind', 'label', 'start_ms', 'end_ms', 'safe_start_boundary_id', 'safe_end_boundary_id', 'text'],
            'blocks': [[b.block_id, b.scene_id, b.kind, b.label, b.start_ms, b.end_ms, edge(b.start_ms), edge(b.end_ms), b.text] for b in episode.blocks],
            'allowed_base_windows': [[s,e,episode.boundaries[e]['ms']-episode.boundaries[s]['ms']] for s,e in base_windows(episode,config)],
            'window_columns':['start_boundary_id','end_boundary_id','duration_ms'],
            'search_policy':'Up to max_base_windows evenly sampled source-valid dialogue-boundary windows; use a complete pair; this cap may miss better base cuts.'}
    if config.get('discovery_version') == 4:
        # Missing episode files must not turn distant supplied episodes into
        # purportedly adjacent story context. Legacy payloads remain unchanged.
        number = int(episode.episode_id.split('-')[-1])
        payload['metadata_context'] = [entry for entry in nearby_context(episode, index)
            if abs(int(entry['episode_id'].split('-')[-1])-number) <= 1]
        payload['search_policy'] = ('Versioned V4 menu: '+config['v4_menu_policy']+
            '; bounded source-valid pairs, no word-prefix estimation. Propose different source events, not nearby trims of the same event.')
    return payload


def validate_analysis(analysis, episode, config):
    if not analysis['proposals'] and not analysis['no_candidate_reason'].strip():
        raise ValueError('Zero proposals needs a reason')
    if len(analysis['proposals']) > config['max_proposals_per_episode']:
        raise ValueError('Too many proposals')
    for proposal in analysis['proposals']:
        cut = candidate(episode, proposal['start_boundary_id'], proposal['end_boundary_id'], 'validation')
        errors = validate_cut(cut, episode, config)
        if errors:
            raise ValueError(f'Invalid proposal boundaries: {errors}; resolved [start_ms,end_ms]={coordinates(cut, episode)}; duration must be 20000-60000 ms')
        for key in ['hook', 'cliffhanger']:
            resolve_block_ids(proposal[key]['evidence_ids'], cut, episode)
        if config.get('discovery_version') == 4:
            if (cut.start_boundary_id, cut.end_boundary_id) not in base_windows(episode, config):
                raise ValueError('V4 proposal is not a complete allowed menu pair')
            resolve_block_ids(proposal['event_anchor_ids'], cut, episode)


def analyze(episode, index, config, client):
    payload = analyst_payload(episode, index, config)
    role = 'v4/analyst' if config.get('discovery_version') == 4 else 'analyst'
    return client.generate(role, payload, analysis_schema(episode, config), lambda a: validate_analysis(a, episode, config))


def base_candidates(episode, analysis):
    result = []
    for p in analysis['proposals']:
        fid = 'family-'+digest([episode.source_sha256, p['start_boundary_id'], p['end_boundary_id']])[:16]
        result.append((candidate(episode, p['start_boundary_id'], p['end_boundary_id'], fid), p))
    return result
