from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CRITERIA = ("hook", "cliffhanger", "comprehension", "emotion", "coherence", "boundary")
WEIGHTS = dict(zip(CRITERIA, (20, 25, 25, 10, 10, 10)))


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256((value if isinstance(value, bytes) else canonical(value).encode())).hexdigest()


def obj(properties, required=None):
    return {"type": "object", "properties": properties, "required": list(properties) if required is None else required,
            "additionalProperties": False}


STR = {"type": "string", "minLength": 1, "maxLength": 400}
STRINGS = {"type": "array", "items": STR, "uniqueItems": True}
IDS = dict(STRINGS, minItems=1)
SCORE = {"type": "integer", "minimum": 0, "maximum": 4}
CLAIM = obj({"text": STR, "evidence_ids": IDS})
CRITERION = {'oneOf': [obj({'score': {'enum': [0, None]}, 'evidence_ids': STRINGS, 'reason': STR}),
                        obj({'score': {'enum': [1,2,3,4]}, 'evidence_ids': IDS, 'reason': STR})]}
SCORES = obj({k: SCORE for k in CRITERIA})
MODEL_CLAIM = obj({'text':dict(STR,maxLength=200),'evidence_ids':dict(IDS,maxItems=4)})
MODEL_NOTES = dict(STRINGS,maxItems=3,items=dict(STR,maxLength=160))
PROPOSAL = obj({"event_summary": dict(STR,maxLength=200), "start_boundary_id": STR, "end_boundary_id": STR,
                "hook": MODEL_CLAIM, "cliffhanger": MODEL_CLAIM, "required_context": MODEL_NOTES,
                "visual_flags": MODEL_NOTES, "scores": SCORES})
ANALYSIS_SCHEMA = obj({"proposals": {"type": "array", "maxItems": 3, "items": PROPOSAL}, "no_candidate_reason": {"type": "string", "maxLength": 300}})
REVIEW_SCHEMA = obj({"interpretation": MODEL_CLAIM, "missing_context": MODEL_NOTES,
                     "essential_context_missing": {"type": "boolean"}, "unresolved_question": MODEL_CLAIM,
                     "criteria": obj({k: CRITERION for k in CRITERIA}), "uncertainty_flags": MODEL_NOTES})


@dataclass
class Block:
    block_id: str
    scene_id: str
    kind: str
    label: str
    speaker: str
    delivery: list[str]
    emotion: str
    start_ms: int
    end_ms: int
    raw_start: str
    raw_end: str
    text: str
    raw_text: str
    line_start: int
    line_end: int
    ordinal: int


@dataclass
class Scene:
    scene_id: str
    heading: str = ""
    start_ms: int | None = None
    end_ms: int | None = None
    line_start: int = 1


@dataclass
class Episode:
    episode_id: str
    source_name: str
    source_sha256: str
    metadata: dict
    scenes: list[Scene]
    blocks: list[Block]
    extent_ms: int
    duration_basis: str
    warnings: list[dict] = field(default_factory=list)
    boundaries: dict = field(default_factory=dict)


@dataclass
class Candidate:
    candidate_id: str
    family_id: str
    episode_id: str
    start_boundary_id: str
    end_boundary_id: str
    variant: str = "base"


DEFAULTS = {
    'discovery_version': 1,
    'v4_menu_policy': 'retain_feasible_refill', 'v4_initial_candidates': 24,
    'v4_extra_reviews': 24, 'v4_extra_families': 8, 'v4_combined_boundary': 1,
    'v4_episode_limit': 12,
    "editorial_gate_version": 3,
    "editorial_version": 2, "minimum_hook_specificity": 1,
    "schema_version": "1.0", "min_duration_ms": 20000, "max_duration_ms": 60000,
    "hook_window_ms": 3000, "max_proposals_per_episode": 1, "max_families": 5,
    "max_variants": 5, "neighbor_radius_ms": 10000, "top_k": 10, "seed": 20261105,
    "provider": "local", "endpoint": "http://127.0.0.1:8080/v1", "model": "qwen3-4b",
    "model_sha256": "unknown", "runtime_identity": "user-supplied",
    "runtime_cache_ram_mib": 0, "runtime_checkpoints": 0,
    "temperature": 0.7, "top_p": 0.8, "top_k_sampling": 20, "max_tokens": 1400,
    "request_timeout_s": 360, "max_attempts": 462, "repair_attempts": 1,
    "max_input_chars": 75000, "cache_dir": "private/cache", "manual_dir": "private/manual",
    "max_base_windows": 48,
    "max_files": 200, "max_member_bytes": 2097152, "max_total_bytes": 52428800,
    "max_compression_ratio": 100,
}


def config_from(path=None):
    config = dict(DEFAULTS)
    if path:
        incoming = json.loads(Path(path).read_text())
        if set(incoming) - set(config):
            raise ValueError(f"Unknown configuration keys: {sorted(set(incoming)-set(config))}")
        config.update(incoming)
    for key in [k for k, v in DEFAULTS.items() if isinstance(v, int)]:
        if type(config[key]) is not int or config[key] < 0:
            raise ValueError(f"{key} must be a nonnegative integer")
    if not 20000 <= config['min_duration_ms'] <= config['max_duration_ms'] <= 60000:
        raise ValueError("Duration policy must stay inside 20-60 seconds")
    if not 1 <= config['max_variants'] <= 5 or config['max_proposals_per_episode'] > 3:
        raise ValueError("At most five variants and three proposals are supported")
    if config['repair_attempts'] > 1 or config['provider'] not in {'local', 'manual', 'none', 'replay'}:
        raise ValueError("Unsupported provider or repair policy")
    if config['hook_window_ms'] != 3000 or not 1 <= config['top_k'] <= 10:
        raise ValueError('The opener is exactly 3000 ms and top_k must be 1..10')
    if not 1 <= config['max_base_windows'] <= 200:
        raise ValueError('max_base_windows must be 1..200')
    if config["editorial_version"] not in {1, 2} or config["minimum_hook_specificity"] not in {1, 2}:
        raise ValueError("Invalid editorial version or opener specificity threshold")
    if config["editorial_gate_version"] not in {2, 3, 4}:
        raise ValueError("Gate version must be 2, 3 or 4")
    if config['discovery_version'] not in {1, 4}:
        raise ValueError('Discovery version must be 1 or 4')
    if config['discovery_version'] == 4 or config['editorial_gate_version'] == 4:
        if config['discovery_version'] != 4 or config['editorial_gate_version'] != 4 or config['editorial_version'] != 2:
            raise ValueError('V4 requires opt-in discovery 4, gate 4 and editorial version 2')
        if (config['max_proposals_per_episode'] not in {1, 2} or config['max_base_windows'] > 48
                or not 1 <= config['v4_initial_candidates'] <= min(24, config['max_families']) or config['v4_extra_reviews'] > 24
                or config['v4_extra_families'] > 8 or config['v4_combined_boundary'] not in {0, 1}
                or not 1 <= config['max_variants'] <= 4 or config['v4_episode_limit'] < 1):
            raise ValueError('V4 exceeds bounded discovery/review policy')
        if config['v4_menu_policy'] not in {'original', 'retain_feasible_refill'}:
            raise ValueError('Unknown V4 menu policy')
    return config


def plain(value):
    return asdict(value)
