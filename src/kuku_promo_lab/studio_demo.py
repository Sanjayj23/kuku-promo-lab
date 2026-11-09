"""Synthetic demo runs for the Studio, made from genuine saved responses only.

Both fixtures in tests/fixtures hold real model responses to an authored
science-fiction screenplay (never Kuku's pack). Replaying them makes zero
model calls; the pipeline re-parses the source, re-validates every saved
response and rebuilds the outputs. Nothing here invents a judgment.
"""
from __future__ import annotations

import json
from pathlib import Path

from .pipeline import run
from . import studio

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO/'tests'/'fixtures'

DEMOS = [
    ('synthetic-v1-replay', 'synthetic_replay',
     'Synthetic demo · V1 rubric',
     'Genuine saved Qwen3-8B responses to the authored "Orbit" screenplay, replayed with zero model calls. '
     'Earlier V1 rubric without the separate opener/ending checks.'),
    ('synthetic-v2-gate3-replay', 'synthetic_replay_v2',
     'Synthetic demo · V2 checks, gate 3',
     'Genuine saved Qwen3-4B responses (6 Nov 2026) to the same screenplay, replayed under the current gate 3 with '
     'zero model calls. Shows the system abstaining: no cut passes every check.'),
    ('synthetic-v2-gate3-v6-source-check', None,
     'Synthetic demo · gate 3 + V6 source check',
     'The gate-3 synthetic run re-read against the script by the V6 source check (zero model calls). It flags the ending '
     'check that called a question "answered" with a line that only restates it. No cut is promoted.'),
]


def make_demo(out='private/studio-demo', fixtures=None):
    fixtures = Path(fixtures) if fixtures else FIXTURES
    source = fixtures/'synthetic'
    if not source.is_dir():
        raise ValueError(f'Synthetic fixtures not found at {fixtures}; run from the repository checkout')
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    labels, results = {}, []
    for name, fixture, label, note in DEMOS:
        target = out/name
        labels[name] = {'label': label, 'note': note, 'synthetic': True}
        if target.exists():
            results.append({'run': name, 'status': 'exists (kept unchanged)'})
            continue
        if fixture is None:
            from .source_check import write_check
            summary = write_check(source, out/'synthetic-v2-gate3-replay', target)
            results.append({'run': name, 'status': 'source check', 'fresh_model_attempts': summary['model_calls'],
                            'recommendations': 0})
            continue
        replay_of = fixtures/fixture
        config = json.loads((replay_of/'run_manifest.json').read_text())['config']
        manifest = run(source, target, config, str(replay_of), 'proposed')
        results.append({'run': name, 'status': manifest['run_status'],
                        'fresh_model_attempts': manifest['inference']['attempts'],
                        'recommendations': manifest['statistics']['recommendations']})
    (out/'studio_demo_labels.json').write_text(json.dumps(labels, indent=2)+'\n')
    return {'out': str(out.resolve()), 'runs': results}


def build_site(demo='private/studio-demo', out='dist/studio-site', public_input=None):
    demo = Path(demo)
    if not all((demo/name).is_dir() for name, *_ in DEMOS):
        make_demo(demo)
    labels = json.loads((demo/'studio_demo_labels.json').read_text())
    runs = [(demo/name, labels[name]['label'], labels[name]['note']) for name, *_ in DEMOS]
    return studio.build_static(runs, out, public_input or FIXTURES/'synthetic')
