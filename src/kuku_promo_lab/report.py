"""Escaped local report: real data and independent alternatives, no video player."""
import html
import json
from pathlib import Path


def render_report(run_dir):
    root = Path(run_dir)
    manifest = json.loads((root/'run_manifest.json').read_text())
    promos = json.loads((root/'promos.json').read_text())['recommendations']
    families = [json.loads(s) for s in (root/'candidate_families.jsonl').read_text().splitlines()]
    episodes = {e['episode_id']:e for e in (json.loads(s) for s in (root/'episodes.jsonl').read_text().splitlines())}
    esc = lambda x: html.escape(str(x))
    cards = []
    for p in promos:
        s = p['segments'][0]
        status = f"Tier {p.get('tier', 'V1')} / human review pending"
        ending_quote = p.get('ending_quote', '')
        boundary = p.get('boundary_decision', {}).get('summary', '')
        cards.append(f"<article><h2>#{p['rank']} · {esc(s['source_episode'])} · {p['ranking']['score']} model points</h2><p>{s['start_timecode']} → {s['end_timecode']} ({p['duration_ms']/1000:g}s)</p><p>{esc(status)} · {esc(p.get('tie_group', ''))}</p><p><b>Hook:</b> {esc(p['hook']['text'])}</p><p><b>Proposed question:</b> {esc(p['cliffhanger']['text'])}</p><p><b>Ending evidence:</b> {esc(ending_quote)}</p><p>{esc(p['why_it_works'])}</p><p><b>Why this boundary:</b> {esc(boundary)}</p><small>{esc(', '.join(p['quality']['flags']))}</small></article>")
    comparisons = []
    for family in families:
        rows = []
        for r in family['variants']:
            result = r.get('review')
            scores = {k: v['score'] for k, v in result['criteria'].items()} if result else {'error': r.get('error')}
            original = [b for b in episodes[family['episode_id']]['blocks'] if b['start_ms'] < r['end_ms'] and b['end_ms'] > r['start_ms']]
            rows.append(f"<details><summary>{esc(r['candidate']['variant'])} · {r['start_ms']/1000:g}–{r['end_ms']/1000:g}s · {esc(r.get('tier', 'V1'))} · {esc(r['eligibility_errors'] or 'passes model gates / human review pending')}</summary><p>{esc(scores)}</p><h4>Restricted packet actually sent</h4><pre>{esc(json.dumps(r['packet'],indent=2,ensure_ascii=False))}</pre><h4>Actual saved review</h4><pre>{esc(json.dumps(result,indent=2,ensure_ascii=False))}</pre><details><summary>Private source evidence (never sent to restricted reviewer)</summary><pre>{esc(json.dumps(original,indent=2,ensure_ascii=False))}</pre></details></details>")
        comparisons.append(f"<article><h2>{esc(family['family_id'])} · {esc(family['episode_id'])}</h2><p>{esc(family['comparison'])}</p>{''.join(rows)}</article>")
    body = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kuku Promo Lab — local review</title><style>body{{font:16px/1.55 system-ui;max-width:1100px;margin:40px auto;padding:0 24px;background:#10151c;color:#e7edf4}}h1,h2{{line-height:1.2}}h1{{font-size:36px}}article{{border:1px solid #354252;border-radius:14px;padding:22px;margin:18px 0;background:#19212b}}a{{color:#8cceff}}small{{color:#abbaca}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#10151c;padding:16px}}summary{{cursor:pointer;padding:14px 0}}.tag{{color:#8de0bc}}</style><h1>Kuku Promo Lab</h1><p class="tag">Private local script review · {esc(manifest['run_status'])} · {esc(manifest['execution'])}</p><p>{esc(manifest['statistics'])}</p><p>Script timestamps only. Dialogue-only model judgments; human review pending; no video processed.</p><p><a href="promos.json">JSON</a> · <a href="promos.csv">CSV</a> · <a href="validation.json">Validation</a> · <a href="run_manifest.json">Run manifest</a></p><h2>Model suggestions for human review ({len(promos)})</h2>{''.join(cards) or '<article>No eligible recommendations. Inspect provisional candidates, rejection reasons and failures.</article>'}<h2>Boundary comparisons</h2>{''.join(comparisons) or '<p>No independently reviewed families available.</p>'}</html>"""
    (root/'report.html').write_text(body)
