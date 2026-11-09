from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .schemas import config_from, obj, STR


def main(argv=None):
    parser = argparse.ArgumentParser(prog='kuku', description='Private source-grounded screenplay promo search')
    commands = parser.add_subparsers(dest='command', required=True)
    doctor = commands.add_parser('doctor'); doctor.add_argument('--config'); doctor.add_argument('--out', default='private/doctor')
    for name in ['run', 'baseline', 'replay']:
        p = commands.add_parser(name)
        p.add_argument('--input', required=True); p.add_argument('--out', required=True); p.add_argument('--config')
        if name == 'run':
            p.add_argument('--reuse-v1', help='Reuse verified exact-match V1 analyst and review records for the same pack')
        if name == 'baseline':
            p.add_argument('--mode', choices=['random', 'direct'], required=True)
        if name == 'replay':
            p.add_argument('--run', required=True)
            p.add_argument('--editorial-gate-version', type=int, choices=[2,3,4])
    p = commands.add_parser('validate'); p.add_argument('--input', required=True); p.add_argument('--run', required=True)
    p = commands.add_parser('demo-select'); p.add_argument('--run', required=True)
    p = commands.add_parser('evaluate'); p.add_argument('--run', required=True); p.add_argument('--out', required=True)
    p.add_argument('--reference-v3', help='Optional immutable V3 run for actual accepted-output count; never substitutes exploratory B cuts')
    p = commands.add_parser('source-check', help='V6: zero-call source grounding of a saved run, written to a NEW directory')
    p.add_argument('--input', required=True); p.add_argument('--run', required=True); p.add_argument('--out', required=True)
    p = commands.add_parser('serve', help='Open Promo Lab Studio on loopback')
    p.add_argument('--config'); p.add_argument('--port', type=int, default=8765)
    p.add_argument('--runs', nargs='*', help='Directories holding saved runs (default: private/studio-demo private/runs)')
    p.add_argument('--reviews-dir', default='private/editor-reviews', help='Where editor decisions are appended (never inside a run)')
    p = commands.add_parser('studio-demo', help='Replay the bundled genuine synthetic fixtures for the Studio (zero model calls)')
    p.add_argument('--out', default='private/studio-demo')
    p = commands.add_parser('studio-build', help='Build a static, synthetic-only Studio site that needs no server')
    p.add_argument('--out', required=True); p.add_argument('--demo', default='private/studio-demo')
    p.add_argument('--public-input', default=str(Path(__file__).resolve().parents[2]/'tests'/'fixtures'/'synthetic'),
                   help='Directory of public input files; every episode hash must match one of these')
    p = commands.add_parser('promos', help='V8: find and rank promos with any OpenAI-compatible model (recommended)')
    p.add_argument('--input', required=True, help='Screenplay pack: .zip or folder of timestamped .txt episodes')
    p.add_argument('--out', required=True, help='New run directory for outputs')
    p.add_argument('--config', default=None, help='JSON config (model, endpoint, limits); default: Gemini via .env key')
    p.add_argument('--episodes', default=None, help='Optional subset, e.g. 1-5 or 3,7,9')
    args = parser.parse_args(argv)
    try:
        if args.command == 'promos':
            from .v8.pipeline import run as run_v8
            episodes = None
            if args.episodes:
                episodes = []
                for part in args.episodes.split(','):
                    lo, _, hi = part.partition('-')
                    episodes += list(range(int(lo), int(hi or lo) + 1))
            manifest = run_v8(args.input, args.out, args.config, episodes, log=lambda m: print(m, flush=True))
            return 0 if not manifest['failures'] else 4
        if args.command == 'validate':
            from .validation import validate_run
            result = validate_run(args.input, args.run)
            print(json.dumps(result, indent=2)); return 0 if result['valid'] else 2
        if args.command == 'demo-select':
            from .evaluate import demo_select
            result = demo_select(args.run)
            print(json.dumps(result, indent=2)); return 0 if result else 3
        if args.command == 'evaluate':
            from .evaluate import evaluate
            print(json.dumps(evaluate(args.run, args.out, getattr(args,'reference_v3',None)), indent=2)); return 0
        if args.command == 'source-check':
            from .source_check import write_check
            print(json.dumps(write_check(args.input, args.run, args.out), indent=2, ensure_ascii=False)); return 0
        if args.command == 'studio-demo':
            from .studio_demo import make_demo
            print(json.dumps(make_demo(args.out), indent=2)); return 0
        if args.command == 'studio-build':
            from .studio_demo import build_site
            print(json.dumps(build_site(args.demo, args.out, args.public_input), indent=2)); return 0
        config = config_from(args.config)
        if args.command == 'serve':
            from .ui import serve
            serve(config, args.port, roots=args.runs, reviews_dir=args.reviews_dir); return 0
        if args.command == 'doctor':
            from .models import ModelClient
            from .export import write_json
            out = Path(args.out)
            if out.exists():
                raise ValueError('Doctor output exists; choose a new path')
            # A connectivity check must not succeed from an earlier cached call.
            doctor_config = dict(config, cache_dir=str(out/'fresh-inference-cache'))
            client = ModelClient(doctor_config, out)
            result = client.generate('doctor', {'synthetic_check': True}, obj({'ok': {'const': True}, 'message': STR}))
            report = {'response': result, 'model': config['model'], 'settings': doctor_config, 'elapsed_s': client.latency_s,
                      'fresh_inference_check': True,
                      'attempts': client.attempts, 'cache_hits': client.cache_hits, 'usage': client.usage}
            write_json(out/'doctor.json', report); print(json.dumps(report, indent=2)); return 0
        if args.command == 'replay':
            manifest = json.loads((Path(args.run)/'run_manifest.json').read_text())
            if args.config:
                raise ValueError('Replay uses original configuration; changing it would change request identities')
            config = manifest['config']
            if args.editorial_gate_version is not None:
                original_gate = config.get('editorial_gate_version',2)
                if (args.editorial_gate_version == 4 or original_gate == 4) and args.editorial_gate_version != original_gate:
                    raise ValueError('V4 requires genuine V4 requests; cross-version regating is unsupported')
                if config.get('editorial_version') != 2:
                    raise ValueError('Gate override requires a V2 run')
                config = dict(config, editorial_gate_version=args.editorial_gate_version)
        from .pipeline import run
        manifest = run(args.input, args.out, config, args.run if args.command == 'replay' else None,
                       args.mode if args.command == 'baseline' else 'proposed', progress=lambda m: print(m, flush=True),
                       prior_dir=getattr(args, 'reuse_v1', None))
        print(json.dumps({'run_status': manifest['run_status'], 'statistics': manifest['statistics'], 'out': str(Path(args.out).resolve())}, indent=2))
        return 0 if manifest['run_status'] == 'complete' else 4
    except (ValueError, RuntimeError, OSError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 3 if args.command == 'doctor' else 2


if __name__ == '__main__':
    raise SystemExit(main())
