"""Promo Lab Studio: read-only adapter, static build guard and local server."""
import hashlib
import http.client
import json
import os
import re
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from kuku_promo_lab import studio
from kuku_promo_lab.studio_demo import make_demo, build_site
from kuku_promo_lab.schemas import config_from

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT/'tests'/'fixtures'/'synthetic'


def tree_hash(path):
    h = hashlib.sha256()
    for p in sorted(Path(path).rglob('*')):
        if p.is_file():
            h.update(p.relative_to(path).as_posix().encode()); h.update(p.read_bytes())
    return h.hexdigest()


class StudioAdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.demo = Path(cls.tmp.name)/'demo'
        cls.result = make_demo(cls.demo)
        cls.v1 = cls.demo/'synthetic-v1-replay'
        cls.v2 = cls.demo/'synthetic-v2-gate3-replay'

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_demo_replays_make_zero_model_calls(self):
        runs = {r['run']: r for r in self.result['runs']}
        self.assertEqual(runs['synthetic-v1-replay']['fresh_model_attempts'], 0)
        self.assertEqual(runs['synthetic-v2-gate3-replay']['fresh_model_attempts'], 0)
        self.assertEqual(runs['synthetic-v1-replay']['recommendations'], 2)
        self.assertEqual(runs['synthetic-v2-gate3-replay']['recommendations'], 0)
        current = self.demo/'synthetic-v2-gate3-v6-source-check'
        self.assertEqual(json.loads((current/'v5_summary.json').read_text())['source_check_version'], 6)
        historical = self.demo/'synthetic-v2-gate3-v5-source-check'
        historical.mkdir(exist_ok=True)
        sentinel = historical/'v5_summary.json'
        sentinel.write_text('{"source_check_version":5}\n')
        before = sentinel.read_bytes()
        make_demo(self.demo)
        self.assertEqual(sentinel.read_bytes(), before)
        labels = json.loads((self.demo/'studio_demo_labels.json').read_text())
        self.assertNotIn(historical.name, labels)
        self.assertIn('V6', labels[current.name]['label'])


    def test_view_copies_saved_results_without_changing_them(self):
        before = tree_hash(self.v1)
        view = studio.load_run(self.v1)
        self.assertEqual(before, tree_hash(self.v1), 'load_run must not modify the run directory')
        promos = json.loads((self.v1/'promos.json').read_text())['recommendations']
        self.assertEqual([r['promo_id'] for r in view['recommendations']], [p['promo_id'] for p in promos])
        self.assertEqual([r['rank'] for r in view['recommendations']], [p['rank'] for p in promos])
        self.assertEqual([r['score'] for r in view['recommendations']], [p['ranking']['score'] for p in promos])
        families = [json.loads(s) for s in (self.v1/'candidate_families.jsonl').read_text().splitlines()]
        self.assertEqual(len(view['families']), len(families))
        for raw, fam in zip(families, view['families']):
            self.assertEqual(fam['winner_id'], raw['comparison']['winner_id'])
            self.assertEqual([v['candidate_id'] for v in fam['variants']], [v['candidate']['candidate_id'] for v in raw['variants']])
            self.assertEqual(sum(v['is_winner'] for v in fam['variants']), 1)
            for v, r in zip(fam['variants'], raw['variants']):
                self.assertEqual((v['start_ms'], v['end_ms']), (r['start_ms'], r['end_ms']))
        statuses = {v['status'] for f in view['families'] for v in f['variants']}
        self.assertTrue(statuses <= {'recommended', 'eligible', 'excluded', 'failed'})
        self.assertEqual(view['counts']['recommended'], 2)

    def test_tiers_and_flags_are_copied_not_recomputed(self):
        view = studio.load_run(self.v2)
        self.assertEqual(view['recommendations'], [])
        families = [json.loads(s) for s in (self.v2/'candidate_families.jsonl').read_text().splitlines()]
        for raw, fam in zip(families, view['families']):
            for v, r in zip(fam['variants'], raw['variants']):
                self.assertEqual(v['tier'], r.get('tier'))
                expected = list(dict.fromkeys(r['eligibility_errors'] + r['flags']))
                self.assertEqual([f['code'] for f in v['flags']], expected)
        self.assertEqual(view['counts']['variant_status'], {'excluded': 5})
        self.assertEqual(view['run']['inference']['fresh_attempts'], 0)

    def test_utterances_map_to_source_blocks_and_hide_nothing_extra(self):
        view = studio.load_run(self.v1, include_source=False)
        for e in view['episodes']:
            for b in e['blocks']:
                self.assertNotIn('text', b)
        v = view['families'][0]['variants'][0]
        self.assertTrue(all(u['source_block'].startswith('ep-') for u in v['utterances']))

    def test_flag_explanations(self):
        self.assertEqual(studio.explain_flag('comprehension_below_3')['severity'], 'block')
        self.assertEqual(studio.explain_flag('hook_uncited')['label'], 'Hook not cited')
        self.assertEqual(studio.explain_flag('question_terms_unverified')['severity'], 'review')
        note = studio.explain_flag("1: The 'buyer' is not identified")
        self.assertEqual(note['area'], 'model_note')
        self.assertEqual(note['explanation'], "The 'buyer' is not identified")

    def test_static_build_is_synthetic_only(self):
        out = Path(self.tmp.name)/'site'
        result = build_site(self.demo, out, SYNTHETIC)
        self.assertIn('index.html', result['files'])
        self.assertIn('runs/synthetic-v1-replay/promos.json', result['files'])
        data = (out/'data.js').read_text()
        self.assertTrue(data.startswith('window.KUKU_STATIC='))
        payload = json.loads(data[len('window.KUKU_STATIC='):].rstrip().rstrip(';'))
        self.assertEqual(sorted(payload['views']), ['synthetic-v1-replay', 'synthetic-v2-gate3-replay', 'synthetic-v2-gate3-v6-source-check'])
        self.assertTrue(all(v['run']['synthetic'] for v in payload['views'].values()))
        self.assertEqual((out/'runs'/'synthetic-v1-replay'/'promos.json').read_bytes(), (self.v1/'promos.json').read_bytes())
        with self.assertRaises(ValueError):
            build_site(self.demo, out, SYNTHETIC)

    def test_static_build_refuses_non_public_sources(self):
        other = Path(self.tmp.name)/'other-inputs'
        other.mkdir()
        (other/'orbit-1.txt').write_text((SYNTHETIC/'orbit-1.txt').read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'Refusing static export'):
            studio.assert_public_safe(self.v1, other)
        self.assertTrue(studio.assert_public_safe(self.v1, SYNTHETIC))

    def test_frontend_never_injects_html(self):
        js = (studio.static_dir()/'app.js').read_text()
        self.assertNotRegex(js, r'innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(')
        html = (studio.static_dir()/'index.html').read_text()
        self.assertNotRegex(html, r'<script>[^<]')  # no inline scripts; CSP is script-src 'self'


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); return s.getsockname()[1]


class StudioServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from kuku_promo_lab.ui import serve
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cwd = os.getcwd()
        os.chdir(cls.tmp.name)
        cls.demo = Path(cls.tmp.name)/'demo'
        make_demo(cls.demo, ROOT/'tests'/'fixtures')
        cls.before = tree_hash(cls.demo)
        cls.port = free_port()
        cls.reviews = Path(cls.tmp.name)/'reviews'
        threading.Thread(target=serve, args=(config_from(None), cls.port),
                         kwargs={'roots': [cls.demo], 'reviews_dir': cls.reviews, 'open_message': False}, daemon=True).start()
        for _ in range(50):
            try:
                socket.create_connection(('127.0.0.1', cls.port), timeout=.2).close(); break
            except OSError:
                time.sleep(.1)
        cls.host = f'127.0.0.1:{cls.port}'

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.cwd)
        cls.tmp.cleanup()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        hdr = {'Host': self.host}; hdr.update(headers or {})
        c.request(method, path, body=body, headers=hdr)
        r = c.getresponse(); data = r.read(); c.close()
        return r.status, data, r

    def token(self):
        status, page, _ = self.req('GET', '/')
        return re.search(rb'name="run-token" content="([^"]+)"', page).group(1).decode()

    def runs(self):
        return json.loads(self.req('GET', '/api/runs')[1])['runs']

    def test_page_assets_and_security_headers(self):
        status, page, r = self.req('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn("script-src 'self'", r.getheader('Content-Security-Policy'))
        self.assertNotIn(b'__RUN_TOKEN__', page)
        self.assertEqual(self.req('GET', '/app.js')[0], 200)
        self.assertEqual(self.req('GET', '/fonts/inter-var.woff2')[0], 200)
        self.assertEqual(self.req('GET', '/data.js')[1], b'window.KUKU_STATIC=null;\n')
        self.assertEqual(self.req('GET', '/fonts/../../ui.py')[0], 404)
        self.assertEqual(self.req('GET', '/', headers={'Host': 'evil.example'})[0], 403)

    def test_run_library_and_views(self):
        runs = self.runs()
        self.assertEqual({r['id'] for r in runs}, {'synthetic-v1-replay', 'synthetic-v2-gate3-replay', 'synthetic-v2-gate3-v6-source-check'})
        self.assertTrue(all(r['synthetic'] for r in runs))
        v1 = next(r for r in runs if r['id'] == 'synthetic-v1-replay')
        status, data, _ = self.req('GET', f"/api/runs/{v1['key']}")
        self.assertEqual(status, 200)
        view = json.loads(data)
        self.assertEqual(len(view['recommendations']), 2)
        self.assertIn('promos.json', view['run']['files'])
        status, raw, r = self.req('GET', f"/api/runs/{v1['key']}/files/promos.json")
        self.assertEqual(raw, (self.demo/'synthetic-v1-replay'/'promos.json').read_bytes())
        self.assertIn('attachment', r.getheader('Content-Disposition'))
        self.assertEqual(self.req('GET', f"/api/runs/{v1['key']}/files/episodes.jsonl")[0], 404)
        self.assertEqual(self.req('GET', '/api/runs/r-000000000000')[0], 404)

    def test_editor_decisions_are_separate_and_validated(self):
        v1 = next(r for r in self.runs() if r['id'] == 'synthetic-v1-replay')
        view = json.loads(self.req('GET', f"/api/runs/{v1['key']}")[1])
        cid = view['recommendations'][0]['promo_id']
        body = json.dumps({'candidate_id': cid, 'decision': 'shortlist', 'note': 'Strong cold open'}).encode()
        path = f"/api/reviews/{v1['key']}"
        self.assertEqual(self.req('POST', path, body, {'Content-Type': 'application/json'})[0], 403)
        tok = self.token()
        h = {'Content-Type': 'application/json', 'X-Run-Token': tok}
        self.assertEqual(self.req('POST', path, body, dict(h, Origin='http://evil.example'))[0], 403)
        bad = json.dumps({'candidate_id': 'cut-not-in-run', 'decision': 'shortlist'}).encode()
        self.assertEqual(self.req('POST', path, bad, h)[0], 400)
        bad = json.dumps({'candidate_id': cid, 'decision': 'approve_and_post'}).encode()
        self.assertEqual(self.req('POST', path, bad, h)[0], 400)
        status, data, _ = self.req('POST', path, body, h)
        self.assertEqual(status, 201)
        row = json.loads(data)
        self.assertEqual(row['source'], 'human_editor')
        self.assertEqual(row['family_id'], view['recommendations'][0]['family_id'])
        listed = json.loads(self.req('GET', path)[1])['decisions']
        self.assertEqual(listed[-1]['decision'], 'shortlist')
        files = list(self.reviews.glob('*.jsonl'))
        self.assertEqual(len(files), 1)
        self.assertEqual(self.before, tree_hash(self.demo), 'decisions must never be written into a run directory')

    def test_upload_requires_token(self):
        self.assertEqual(self.req('POST', '/run', b'PK', {'Content-Type': 'application/zip'})[0], 403)


if __name__ == '__main__':
    unittest.main()
