"""Promo Lab Studio: a loopback-only local server for the saved-run viewer.

Routes
  GET  /                     Studio single-page app
  GET  /app.js /app.css /fonts/*  static assets packaged with the library
  GET  /api/runs             saved runs found under the configured roots
  GET  /api/runs/<key>       read-only view model (studio.load_run)
  GET  /api/runs/<key>/files/<name>   original exported files (JSON/CSV/HTML)
  GET  /api/reviews/<key>    editor decisions recorded for that run
  POST /api/reviews/<key>    append one editor decision (token required)
  POST /run                  upload a ZIP and start a live run (model server needed)
  GET  /status?id=<job>      progress of a live run

Runs are never modified. Editor decisions are appended to a separate JSONL file
per run under ``reviews_dir`` and are labelled as human input.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .pipeline import run
from . import studio

ASSET_TYPES = {'.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.woff2': 'font/woff2',
               '.html': 'text/html; charset=utf-8', '.svg': 'image/svg+xml', '.txt': 'text/plain; charset=utf-8'}
RUN_FILES = {'report.html': 'text/html; charset=utf-8', 'promos.json': 'application/json', 'promos.csv': 'text/csv; charset=utf-8',
             'validation.json': 'application/json', 'run_manifest.json': 'application/json', 'coverage.json': 'application/json',
             'candidate_families.jsonl': 'application/x-ndjson', 'provisional.json': 'application/json',
             'failures.jsonl': 'application/x-ndjson', 'rejections.jsonl': 'application/x-ndjson',
             'v5_editor_briefs.csv': 'text/csv; charset=utf-8', 'v5_summary.json': 'application/json',
             'v5_source_check.jsonl': 'application/x-ndjson', 'v5_analyst_audit.jsonl': 'application/x-ndjson'}
DECISIONS = {'shortlist', 'reject', 'needs_changes'}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


def run_key(path):
    return 'r-' + hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:12]


class Library:
    """Maps opaque keys to saved run directories; nothing outside the roots is reachable."""

    def __init__(self, roots, labels=None):
        self.roots = [Path(r) for r in roots]
        self.labels = labels or {}

    def runs(self):
        out = {}
        for path in studio.discover_runs(self.roots):
            out[run_key(path)] = path
        return out

    def get(self, key):
        return self.runs().get(key)

    def summaries(self):
        items = []
        for key, path in self.runs().items():
            meta = self.labels.get(path.name, {})
            try:
                s = studio.run_summary(path, meta.get('label'), meta.get('note'), meta.get('synthetic'))
            except (OSError, ValueError, KeyError) as exc:
                s = {'id': path.name, 'label': path.name, 'error': str(exc)}
            s['key'] = key
            items.append(s)
        items.sort(key=lambda s: (not s.get('synthetic'), s.get('finished_utc') or ''), reverse=False)
        return items


def demo_labels(root):
    """Optional display labels: studio_demo_labels.json (written by `kuku studio-demo`)
    or a hand-written studio_labels.json in any run root."""
    out = {}
    for name in ('studio_demo_labels.json', 'studio_labels.json'):
        path = Path(root)/name
        if path.is_file():
            out.update(json.loads(path.read_text()))
    return out


def serve(config, port=8765, roots=None, reviews_dir='private/editor-reviews', open_message=True):
    if not 1024 <= port <= 65535:
        raise ValueError('Choose a local port between 1024 and 65535')
    web_runs = Path('private/web-runs').resolve(); web_runs.mkdir(parents=True, exist_ok=True)
    inputs = Path('private/web-inputs').resolve(); inputs.mkdir(parents=True, exist_ok=True)
    reviews = Path(reviews_dir).resolve(); reviews.mkdir(parents=True, exist_ok=True)
    roots = [Path(r).resolve() for r in (roots or ['private/studio-demo', 'private/runs'])] + [web_runs]
    labels = {}
    for r in roots:
        labels.update(demo_labels(r))
    library = Library(roots, labels)
    assets = studio.static_dir()
    jobs, busy, review_lock = {}, threading.Lock(), threading.Lock()
    token = secrets.token_urlsafe(32)
    host = f'127.0.0.1:{port}'

    def work(job_id, path):
        try:
            manifest = run(path, web_runs/job_id, config, progress=lambda m: jobs[job_id].update(message=m))
            jobs[job_id].update(state='finished', message='Run '+manifest['run_status'], summary=manifest['statistics'],
                                key=run_key(web_runs/job_id))
        except Exception as exc:  # surfaced to the UI as a failed job
            jobs[job_id].update(state='failed', message=str(exc))
        finally:
            busy.release()

    def review_file(path):
        manifest = json.loads((path/'run_manifest.json').read_text())
        rid = ''.join(ch for ch in str(manifest.get('run_id', path.name)) if ch.isalnum() or ch in '-_')
        return reviews/f'{rid}.jsonl', manifest

    class Handler(BaseHTTPRequestHandler):
        def response(self, code, data, mime='application/json', extra=None):
            raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code); self.send_header('Content-Type', mime); self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', CSP)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers(); self.wfile.write(raw)

        def valid_host(self):
            return self.headers.get('Host') == host

        def do_GET(self):
            if not self.valid_host():
                return self.response(403, {'error': 'Invalid host'})
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path in ('/', '/index.html'):
                page = (assets/'index.html').read_text().replace('__RUN_TOKEN__', token)
                return self.response(200, page.encode(), 'text/html; charset=utf-8')
            if path == '/data.js':
                return self.response(200, b'window.KUKU_STATIC=null;\n', ASSET_TYPES['.js'])
            if path in ('/app.js', '/app.css') or path.startswith('/fonts/'):
                name = path.lstrip('/')
                target = (assets/name).resolve()
                if assets.resolve() in target.parents and target.is_file() and target.suffix in ASSET_TYPES:
                    return self.response(200, target.read_bytes(), ASSET_TYPES[target.suffix])
                return self.response(404, {'error': 'Not found'})
            if path == '/status':
                job_id = urllib.parse.parse_qs(parsed.query).get('id', [''])[0]
                return self.response(200, jobs[job_id]) if job_id in jobs else self.response(404, {'error': 'Unknown run'})
            parts = path.strip('/').split('/')
            if parts == ['api', 'runs']:
                return self.response(200, {'mode': 'server', 'runs': library.summaries()})
            if len(parts) >= 3 and parts[:2] == ['api', 'runs']:
                target = library.get(parts[2])
                if target is None:
                    return self.response(404, {'error': 'Unknown run'})
                if len(parts) == 3:
                    meta = library.labels.get(target.name, {})
                    try:
                        view = studio.load_run(target, include_source=True, label=meta.get('label'),
                                               note=meta.get('note'), synthetic=meta.get('synthetic'))
                    except (OSError, ValueError, KeyError) as exc:
                        return self.response(500, {'error': f'Could not read run: {exc}'})
                    view['run']['key'] = parts[2]
                    view['run']['files'] = [n for n in RUN_FILES if (target/n).is_file()]
                    return self.response(200, view)
                if len(parts) == 5 and parts[3] == 'files' and parts[4] in RUN_FILES and (target/parts[4]).is_file():
                    return self.response(200, (target/parts[4]).read_bytes(), RUN_FILES[parts[4]],
                                         {'Content-Disposition': f'attachment; filename="{target.name}-{parts[4]}"'})
                return self.response(404, {'error': 'Not found'})
            if len(parts) == 3 and parts[:2] == ['api', 'reviews']:
                target = library.get(parts[2])
                if target is None:
                    return self.response(404, {'error': 'Unknown run'})
                file, _ = review_file(target)
                rows = [json.loads(s) for s in file.read_text().splitlines() if s.strip()] if file.is_file() else []
                return self.response(200, {'storage': 'server', 'path': str(file), 'decisions': rows})
            # Backwards-compatible direct links to a live web run's files
            if len(parts) == 3 and parts[0] == 'runs' and parts[1] in jobs and parts[2] in RUN_FILES:
                target = web_runs/parts[1]/parts[2]
                if target.is_file():
                    return self.response(200, target.read_bytes(), RUN_FILES[parts[2]])
            self.response(404, {'error': 'Not found'})

        def authorised(self, mime):
            return (self.valid_host() and self.headers.get('X-Run-Token') == token
                    and self.headers.get('Content-Type') == mime
                    and self.headers.get('Origin') in {None, 'http://'+host})

        def do_POST(self):
            parts = urllib.parse.urlparse(self.path).path.strip('/').split('/')
            if len(parts) == 3 and parts[:2] == ['api', 'reviews']:
                return self.post_review(parts[2])
            if self.path != '/run' or not self.authorised('application/zip'):
                return self.response(403, {'error': 'Invalid local upload request'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                return self.response(400, {'error': 'Invalid length'})
            if not 0 < length <= config['max_total_bytes']:
                return self.response(413, {'error': 'ZIP exceeds upload limit'})
            if not busy.acquire(blocking=False):
                return self.response(409, {'error': 'A local run is already in progress'})
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError('Incomplete upload')
                job_id = secrets.token_hex(12); path = inputs/f'{job_id}.zip'; path.write_bytes(raw)
                jobs[job_id] = {'state': 'running', 'message': 'Validating input'}
                threading.Thread(target=work, args=(job_id, path), daemon=True).start()
                self.response(202, {'id': job_id})
            except Exception as exc:
                busy.release(); self.response(400, {'error': str(exc)})

        def post_review(self, key):
            if not self.authorised('application/json'):
                return self.response(403, {'error': 'Invalid local review request'})
            target = library.get(key)
            if target is None:
                return self.response(404, {'error': 'Unknown run'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 8192:
                    raise ValueError('Review body must be 1–8192 bytes')
                body = json.loads(self.rfile.read(length))
                decision = body.get('decision')
                if decision not in DECISIONS:
                    raise ValueError('Decision must be shortlist, reject or needs_changes')
                cid = str(body.get('candidate_id', ''))
                families = [json.loads(s) for s in (target/'candidate_families.jsonl').read_text().splitlines() if s.strip()]
                owner = next((f['family_id'] for f in families for v in f['variants'] if v['candidate']['candidate_id'] == cid), None)
                if owner is None:
                    raise ValueError('Candidate is not part of this run')
                note = str(body.get('note', ''))[:1000]
                editor = str(body.get('editor', ''))[:80]
            except (ValueError, json.JSONDecodeError, AttributeError) as exc:
                return self.response(400, {'error': str(exc)})
            file, manifest = review_file(target)
            row = {'recorded_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'source': 'human_editor',
                   'run_id': manifest.get('run_id'), 'candidate_id': cid, 'family_id': owner,
                   'decision': decision, 'note': note, 'editor': editor}
            with review_lock, file.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(row, ensure_ascii=False)+'\n')
            self.response(201, row)

        def log_message(self, fmt, *args):
            if open_message:
                print(fmt % args, flush=True)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    if open_message:
        print(f'Kuku Promo Lab Studio: http://{host} (local only)', flush=True)
        print('Run library: ' + ', '.join(str(r) for r in roots), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
