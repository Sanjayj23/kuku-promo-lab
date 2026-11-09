/* Kuku Promo Lab Studio — read-only viewer for saved runs.
   It displays what a run saved. It never calls a model, never changes tiers,
   and keeps editor decisions separate from model output. */
(() => {
'use strict';

const STATIC = window.KUKU_STATIC || null;
const TOKEN = (document.querySelector('meta[name="run-token"]') || {}).content || '';
const TABS = [
  {id: 'overview', label: 'Overview'},
  {id: 'promos', label: 'Promos', tone: 'teal'},
  {id: 'review', label: 'Needs review', tone: 'amber'},
  {id: 'excluded', label: 'Excluded'},
  {id: 'lab', label: 'Cut Lab'},
  {id: 'exports', label: 'Run & export'},
];
const STATUS = {
  recommended: {label: 'Recommended', tone: 'teal'},
  accepted: {label: 'Passed gates · not ranked', tone: 'teal'},
  eligible: {label: 'Passed V1 gates · alternative', tone: 'teal'},
  review: {label: 'Needs editorial review', tone: 'amber'},
  excluded: {label: 'Excluded', tone: 'outline'},
  failed: {label: 'Review failed', tone: 'red'},
};
const SHORT = {recommended: 'recommended', accepted: 'passed', eligible: 'passed', review: 'review', excluded: 'excluded', failed: 'failed'};
const DECISION_LABEL = {shortlist: 'Shortlist', needs_changes: 'Needs changes', reject: 'Reject'};
const state = {runs: [], key: null, view: null, decisions: [], storage: 'server', route: {}};

/* ---------- tiny DOM helper ---------- */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k === 'style') for (const [p, val] of Object.entries(v)) el.style.setProperty(p, val);
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const svgNS = 'http://www.w3.org/2000/svg';
function icon(name) {
  const paths = {
    empty: 'M4 12h16M4 12l4-4M4 12l4 4M20 6v12',
    review: 'M12 8v5M12 16.5v.5M10.3 3.9 2.6 17.5A2 2 0 0 0 4.3 20.5h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z',
    check: 'M5 12.5l4.5 4.5L19 7.5',
    file: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5',
  };
  const s = document.createElementNS(svgNS, 'svg');
  s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('width', '24'); s.setAttribute('height', '24');
  s.setAttribute('aria-hidden', 'true'); s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor');
  s.setAttribute('stroke-width', '1.6'); s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round');
  const p = document.createElementNS(svgNS, 'path'); p.setAttribute('d', paths[name] || paths.file); s.append(p);
  return s;
}

/* ---------- formatting ---------- */
const pad = (n, w = 2) => String(n).padStart(w, '0');
function tc(ms) {
  if (ms === null || ms === undefined) return '—';
  const t = Math.max(0, ms) / 1000, m = Math.floor(t / 60), s = t - m * 60;
  return `${pad(m)}:${s < 10 ? '0' : ''}${s.toFixed(1)}`;
}
const secs = ms => `${(ms / 1000).toFixed(ms % 1000 ? 1 : 0)} s`;
const ep = id => String(id || '').replace(/^ep-0*/i, 'Ep ');
const scoreText = s => (s === null || s === undefined) ? '—' : (Math.round(s * 100) / 100).toString();
const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;
function chip(text, tone, dot) { return h('span', {class: `chip ${tone || ''}`}, dot ? h('span', {class: 'dot'}) : null, text); }
function statusChip(s) { const st = STATUS[s] || {label: s, tone: ''}; return chip(st.label, st.tone, true); }
function toast(msg) {
  const t = document.getElementById('toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.remove('show'), 2600);
}

/* ---------- data access ---------- */
async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
  return data;
}
async function listRuns() {
  if (STATIC) return STATIC.runs.map(r => ({...r, key: r.id}));
  return (await getJSON('/api/runs')).runs;
}
async function loadRun(key) {
  if (STATIC) return STATIC.views[key];
  return getJSON(`/api/runs/${encodeURIComponent(key)}`);
}
function fileUrl(name) {
  return STATIC ? `runs/${encodeURIComponent(state.key)}/${name}` : `/api/runs/${encodeURIComponent(state.key)}/files/${name}`;
}
const lsKey = () => `kuku-decisions:${state.view.run.run_id}`;
async function loadDecisions() {
  if (STATIC) {
    state.storage = 'browser';
    try { state.decisions = JSON.parse(localStorage.getItem(lsKey()) || '[]'); } catch { state.decisions = []; }
    return;
  }
  try { const d = await getJSON(`/api/reviews/${encodeURIComponent(state.key)}`); state.decisions = d.decisions; state.storage = 'server'; state.reviewPath = d.path; }
  catch { state.decisions = []; }
}
async function saveDecision(row) {
  if (STATIC) {
    const rec = {recorded_utc: new Date().toISOString().replace(/\.\d+Z$/, 'Z'), source: 'human_editor', run_id: state.view.run.run_id, ...row};
    state.decisions.push(rec);
    try { localStorage.setItem(lsKey(), JSON.stringify(state.decisions)); } catch { /* private mode: keep in memory */ }
    return rec;
  }
  const rec = await getJSON(`/api/reviews/${encodeURIComponent(state.key)}`, {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Run-Token': TOKEN}, body: JSON.stringify(row)});
  state.decisions.push(rec);
  return rec;
}
const latestDecision = cid => [...state.decisions].reverse().find(d => d.candidate_id === cid);

/* ---------- routing ---------- */
function readRoute() {
  const p = new URLSearchParams(location.hash.replace(/^#/, ''));
  return {tab: p.get('tab') || 'overview', run: p.get('run'), family: p.get('family'), cut: p.get('cut')};
}
function go(patch) {
  const r = {...readRoute(), run: state.key, ...patch};
  const p = new URLSearchParams();
  for (const k of ['tab', 'run', 'family', 'cut']) if (r[k]) p.set(k, r[k]);
  const next = '#' + p.toString();
  if (location.hash === next) render(); else location.hash = next;
}

/* ---------- derived helpers ---------- */
const V = () => state.view;
const families = () => V().families;
const familyById = id => families().find(f => f.family_id === id);
const episodeById = id => V().episodes.find(e => e.episode_id === id);
const blockById = id => { for (const e of V().episodes) { const b = e.blocks.find(x => x.id === id); if (b) return b; } return null; };
function representative(f) {
  const w = f.variants.find(v => v.candidate_id === f.winner_id);
  const same = f.variants.filter(v => v.status === f.status).sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
  return (w && w.status === f.status) ? w : (same[0] || w || f.variants[0]);
}
const reviewFamilies = () => families().filter(f => f.status === 'review')
  .sort((a, b) => (representative(b).score ?? -1) - (representative(a).score ?? -1));
const excludedFamilies = () => families().filter(f => f.status === 'excluded' || f.status === 'failed');
function issueList(flags, includeInfo) {
  const seen = new Set(), out = [];
  for (const f of flags) {
    const key = f.area === 'model_note' ? f.code : f.label;
    if (seen.has(key) || (!includeInfo && f.severity === 'info')) continue;
    seen.add(key); out.push(f);
  }
  const order = {block: 0, review: 1, info: 2};
  return out.sort((a, b) => order[a.severity] - order[b.severity]);
}

/* ---------- shell ---------- */
function renderRunPicker() {
  const sel = document.getElementById('run-select');
  sel.replaceChildren(...state.runs.map(r => h('option', {value: r.key, selected: r.key === state.key},
    `${r.label}${r.label === r.id ? '' : ''} — ${r.version || ''} · ${r.status || ''}`)));
  sel.onchange = () => go({run: sel.value, family: null, cut: null});
}
function renderRunMeta() {
  const r = V().run, inf = r.inference;
  const replay = r.execution === 'replay';
  const box = document.getElementById('run-meta');
  box.replaceChildren(
    r.synthetic ? chip('Synthetic data', 'synthetic') : chip('Private pack · local only', 'outline'),
    chip(r.version, 'outline'),
    chip(r.status === 'complete' ? 'Complete run' : 'Partial run', r.status === 'complete' ? 'teal' : 'amber', true),
    chip(replay ? `Replay · ${inf.fresh_attempts} fresh model calls` : `${inf.fresh_attempts} fresh calls · ${inf.cache_hits} cached`, 'outline'),
  );
}
function renderTabs(active) {
  const counts = {promos: V().recommendations.length, review: reviewFamilies().length,
    excluded: excludedFamilies().length, lab: families().length};
  const bar = document.getElementById('tabs');
  bar.replaceChildren(...TABS.map(t => h('button', {
    class: 'tab', role: 'tab', id: `tab-${t.id}`, 'aria-selected': String(t.id === active), 'aria-controls': 'view',
    tabindex: t.id === active ? '0' : '-1', 'data-tone': counts[t.id] ? t.tone : null,
    onclick: () => go({tab: t.id}),
    onkeydown: e => {
      const i = TABS.findIndex(x => x.id === t.id);
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        const n = TABS[(i + (e.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length];
        go({tab: n.id}); setTimeout(() => document.getElementById(`tab-${n.id}`)?.focus(), 30);
      }
    }}, t.label, counts[t.id] !== undefined ? h('span', {class: 'count'}, counts[t.id]) : null)));
}

/* ---------- overview ---------- */
function heroViz() {
  const lines = [[2, 9], [12, 21], [24, 31], [34, 46], [49, 58], [61, 70], [73, 82], [85, 96]];
  const acts = [[0, 14], [44, 60], [80, 100]];
  const rows = [['Analyst cut', 18, 46, 'review'], ['Earlier start', 8, 56, 'review'], ['Later end', 18, 58, 'chosen'], ['Later start', 26, 50, 'review']];
  return h('div', {class: 'cutviz', 'aria-hidden': 'true'},
    h('div', {class: 'eyebrow'}, h('span', {}, 'Counterfactual boundary search'), h('span', {class: 'mono'}, 'illustration')),
    h('div', {class: 'cv-script'},
      lines.map(([a, b]) => h('span', {class: 'cv-line', style: {left: a + '%', width: (b - a) + '%'}})),
      acts.map(([a, b]) => h('span', {class: 'cv-line act', style: {left: a + '%', width: (b - a) + '%'}})),
      h('span', {class: 'cv-window'})),
    h('div', {class: 'cv-rows'}, rows.map(([n, a, w, s], i) => h('div', {class: 'cv-row' + (i === 2 ? ' win' : '')},
      h('span', {}, n), h('span', {class: 'cv-bar'}, h('span', {style: {left: a + '%', width: w + '%'}})), h('span', {class: 'mono'}, s)))),
    h('p', {class: 'cv-caption'}, 'Same event, different edges. Each cut is reviewed on its own; teal is the first 3 seconds, coral the out point. Schematic only. Open the Cut Lab for a real saved comparison.'));
}
function viewOverview() {
  const v = V(), r = v.run, c = v.counts;
  const nReview = reviewFamilies().length, nReviewVariants = c.variant_status.review || 0;
  const nExcl = excludedFamilies().length;
  const recs = v.recommendations.length;
  const hero = h('section', {class: 'hero'},
    h('div', {},
      h('p', {class: 'eyebrow'}, 'Kuku Promo Lab · Problem 1, find the promos'),
      h('h1', {}, 'The cut is ', h('em', {}, 'the product.')),
      h('p', {class: 'hero-copy'}, 'Finding a dramatic scene is not enough. Promo Lab searches for where a 20–60 second promo should start and end, re-reads each candidate the way a new viewer would, and shows the evidence behind every call, including when it should not recommend anything.'),
      h('div', {class: 'row'},
        h('button', {class: 'btn primary', onclick: () => go({tab: 'lab'})}, 'Open the Cut Lab'),
        h('button', {class: 'btn', onclick: () => go({tab: recs ? 'promos' : 'review'})}, recs ? 'See ranked promos' : 'See the review queue')),
      h('div', {class: 'steps'},
        h('div', {class: 'step'}, h('b', {}, 'Parse'), 'Exact script timestamps; no cut lands mid-line.'),
        h('div', {class: 'step'}, h('b', {}, 'Propose'), 'A model reads each episode and names one event.'),
        h('div', {class: 'step'}, h('b', {}, 'Search edges'), 'Nearby starts and ends become separate cuts.'),
        h('div', {class: 'step'}, h('b', {}, 'Re-read cold'), 'Each cut is judged from its own dialogue only.'))),
    heroViz());

  let notice;
  if (!recs) {
    notice = h('div', {class: 'notice amber'}, h('span', {class: 'glyph'}, '0'),
      h('div', {}, h('b', {}, 'No cuts met every automatic editorial gate.'),
        h('span', {}, nReview ? `${plural(nReview, 'event')} (${plural(nReviewVariants, 'cut variant')}) need editorial review; ${plural(nExcl, 'event')} were excluded. Review flagged prospects or inspect the rejection reasons.`
          : `${plural(nExcl, 'event')} were excluded. Inspect the rejection reasons to see what the checks caught.`)),
      h('button', {class: 'btn', onclick: () => go({tab: nReview ? 'review' : 'excluded'})}, nReview ? 'Open review queue' : 'See why'));
  } else {
    const v1 = r.version === 'V1';
    notice = h('div', {class: 'notice teal'}, h('span', {class: 'glyph'}, String(recs)),
      h('div', {}, h('b', {}, v1 ? `${plural(recs, 'cut')} passed the historical V1 rubric.` : `${plural(recs, 'cut')} passed every automatic gate.`),
        h('span', {}, v1 ? 'Earlier rubric with no isolated opener or ending check. Treat as model suggestions; human review is pending.'
          : 'Model suggestions only. An editor must approve before anything is posted.')),
      h('button', {class: 'btn', onclick: () => go({tab: 'promos'})}, 'Open ranked promos'));
  }

  const max = Math.max(c.episodes || 1, 1);
  const step = (i, num, lbl, sub, frac) => h('div', {class: 'fstep', style: {'--i': i}},
    h('div', {class: 'num'}, num ?? '—'), h('div', {class: 'lbl'}, lbl), h('div', {class: 'sub'}, sub),
    h('div', {class: 'meter'}, h('span', {style: {width: Math.round(Math.min(1, frac) * 100) + '%'}})));
  const funnel = h('div', {class: 'funnel', role: 'list', 'aria-label': 'Run funnel'},
    step(0, c.episodes, 'Episodes parsed', `${(c.blocks || 0).toLocaleString()} timed script blocks`, 1),
    step(1, c.base_candidates, 'Events proposed', 'source-valid analyst picks', (c.base_candidates || 0) / max),
    step(2, c.families_reviewed, 'Events searched', 'shortlisted for boundary search', c.families_reviewed / max),
    step(3, c.variants, 'Cuts compared', `${c.variants_reviewed} with a valid full review`, c.variants / Math.max(c.variants, max)),
    h('div', {class: 'fstep out', style: {'--i': 4}},
      h('div', {class: 'lbl'}, 'Outcome by event'),
      h('div', {class: 'outcomes'},
        h('div', {class: 'outcome teal'}, h('span', {}, 'Recommended'), h('b', {}, recs)),
        r.has_tiers ? h('div', {class: 'outcome amber'}, h('span', {}, 'Needs review'), h('b', {}, nReview)) : null,
        h('div', {class: 'outcome'}, h('span', {}, 'Excluded'), h('b', {}, nExcl)),
        h('div', {class: 'outcome' + (c.failures ? ' red' : '')}, h('span', {}, 'Failed checks'), h('b', {}, c.failures)))));

  const famByEp = {};
  for (const f of families()) (famByEp[f.episode_id] ||= []).push(f);
  const rankS = s => ['recommended', 'accepted', 'eligible', 'review', 'excluded', 'failed'].indexOf(s);
  const cov = h('div', {class: 'coverage'}, v.episodes.map(e => {
    const fs = famByEp[e.episode_id] || [];
    const best = fs.map(f => f.status).sort((a, b) => rankS(a) - rankS(b))[0];
    const label = `${ep(e.episode_id)}: ${e.analysis_status || 'parsed'}${fs.length ? `, ${plural(fs.length, 'event')} searched (${(STATUS[best] || {}).label})` : ', not shortlisted'}`;
    return h('button', {class: 'ep' + (fs.length ? ' has' : ''), 'data-s': best || null, 'data-fail': String(e.analysis_status && e.analysis_status !== 'analyzed'),
      title: label, 'aria-label': label, disabled: fs.length ? null : true,
      onclick: fs.length ? () => go({tab: 'lab', family: fs[0].family_id, cut: null}) : null},
    e.episode_id.replace(/^ep-0*/, ''), h('i'));
  }));
  const flagRows = Object.entries(v.flag_counts).slice(0, 8);
  const glossary = v.glossary;
  const fmax = Math.max(1, ...flagRows.map(([, n]) => n));
  const flagsChart = flagRows.length ? h('div', {class: 'bars'}, flagRows.map(([code, n]) => {
    const g = glossary[code] || {label: code.replace(/_/g, ' '), severity: 'block', explanation: ''};
    return h('div', {class: 'barrow', title: g.explanation}, h('span', {}, g.label), h('span', {class: 'n'}, n),
      h('div', {class: 'track'}, h('span', {class: g.severity, style: {width: (n / fmax * 100) + '%'}})));
  })) : h('p', {class: 'muted small'}, 'No flags were raised in this run.');

  const inf = r.inference;
  const prov = h('div', {class: 'prov'},
    kv('Model', r.model.name || '—', true), kv('Execution', r.execution === 'replay' ? `Replay of saved responses · ${inf.replays} reused · ${inf.fresh_attempts} fresh calls` : `${inf.fresh_attempts} fresh attempts · ${inf.cache_hits} cache hits${inf.historical_reuses ? ` · ${inf.historical_reuses} historical reuses` : ''}`),
    kv('Saved validation', v.validation ? (v.validation.valid ? `Passed · ${v.validation.errors} errors · ${plural(v.validation.recommendations_checked, 'recommendation')} checked` : `Failed · ${v.validation.errors} errors`) : 'Not saved'),
    kv('Human review', 'Pending · no ratings recorded'),
    kv('Timing basis', 'Screenplay timestamps; not verified against video'),
    kv('Gate version', r.gate_version ? `Editorial gate ${r.gate_version}` : 'V1 eligibility rules'),
    kv('Finished (UTC)', (r.finished_utc || '—').replace('T', ' ').replace(/(\.\d+)?(\+00:00|Z)$/, ''), true),
    kv('Run ID', r.run_id, true));

  return h('div', {},
    hero,
    h('section', {class: 'section'},
      h('div', {class: 'run-head'},
        h('div', {}, h('p', {class: 'eyebrow'}, 'This run'), h('h2', {class: 'h2'}, r.label),
          r.note ? h('p', {class: 'lede small'}, r.note) : null)),
      notice, sourceCheckCard(), funnel,
      h('div', {class: 'two'},
        h('div', {class: 'card panel'}, h('div', {class: 'section-head'}, h('h3', {class: 'h3'}, 'Episode coverage'),
          h('span', {class: 'muted xs'}, 'Select an episode to open its event in the Cut Lab')), cov,
          h('div', {class: 'legend'},
            h('span', {}, h('i', {style: {background: 'var(--teal)'}}), 'Passed gates'),
            h('span', {}, h('i', {style: {background: 'var(--amber)'}}), 'Needs review'),
            h('span', {}, h('i', {style: {background: 'var(--slate)'}}), 'Excluded'),
            h('span', {}, h('i', {style: {background: 'var(--surface-4)'}}), 'Not shortlisted'))),
        h('div', {class: 'card panel'}, h('div', {class: 'section-head'}, h('h3', {class: 'h3'}, 'What held cuts back'),
          h('span', {class: 'muted xs'}, 'Per cut variant')), flagsChart)),
      h('div', {class: 'card panel', style: {'margin-top': 'var(--s4)'}},
        h('div', {class: 'section-head'}, h('h3', {class: 'h3'}, 'Provenance'), h('span', {class: 'muted xs'}, 'From the saved run manifest')), prov)));
}
function kv(k, v, mono) { return h('div', {class: 'kv'}, h('div', {class: 'k'}, k), h('div', {class: 'v' + (mono ? ' mono' : '')}, v)); }

/* ---------- shared components ---------- */
function miniTrack(episodeId, start, end, endEvidence) {
  const e = episodeById(episodeId);
  const d0 = Math.max(0, start - 12000), d1 = Math.min(e ? e.extent_ms : end + 12000, end + 12000);
  const pct = ms => `${((ms - d0) / (d1 - d0) * 100).toFixed(2)}%`;
  const blocks = (e ? e.blocks : []).filter(b => b.kind === 'dialogue' && b.end_ms > d0 && b.start_ms < d1);
  return h('div', {class: 'minitrack', role: 'img', 'aria-label': `Cut from ${tc(start)} to ${tc(end)} within ${ep(episodeId)}; first three seconds highlighted.`},
    blocks.map(b => h('span', {class: 'blk', style: {left: pct(Math.max(b.start_ms, d0)), width: `calc(${pct(Math.min(b.end_ms, d1))} - ${pct(Math.max(b.start_ms, d0))})`}})),
    h('span', {class: 'sel', style: {left: pct(start), width: `calc(${pct(end)} - ${pct(start)})`}}),
    h('span', {class: 'hookw', style: {left: pct(start), width: `calc(${pct(Math.min(end, start + 3000))} - ${pct(start)})`}}),
    h('span', {class: 'endm', style: {left: `calc(${pct(end)} - 1.5px)`}}));
}
function decisionControls(cid, fid) {
  const wrap = h('div', {class: 'decision-wrap', style: {width: '100%'}});
  const draw = () => {
    const cur = latestDecision(cid);
    const note = h('input', {type: 'text', maxlength: '1000', placeholder: 'Optional editor note (why?)', 'aria-label': 'Editor note', value: cur ? cur.note || '' : ''});
    const buttons = Object.entries(DECISION_LABEL).map(([k, label]) => h('button', {class: 'btn', 'data-decision': k,
      'aria-pressed': String(!!cur && cur.decision === k),
      onclick: async ev => {
        ev.currentTarget.disabled = true;
        try { await saveDecision({candidate_id: cid, family_id: fid, decision: k, note: note.value.trim(), editor: ''}); toast(`Editor decision saved: ${label}`); draw(); }
        catch (e) { toast(e.message); ev.currentTarget.disabled = false; }
      }}, label));
    wrap.replaceChildren(
      h('div', {class: 'decision'}, h('span', {class: 'label'}, 'Your editorial call'), buttons),
      h('div', {class: 'decision-note'}, note),
      h('p', {class: 'recorded'}, cur ? ['Recorded ', h('b', {}, DECISION_LABEL[cur.decision]), ` · ${cur.recorded_utc.replace('T', ' ').replace('Z', ' UTC')} · `] : 'No editor decision yet. ',
        state.storage === 'server' ? 'Saved on this machine, separate from model output.' : 'Stored only in this browser (static demo).'));
  };
  draw();
  return wrap;
}
const unquote = t => String(t || '').trim().replace(/^["\u201C]+|["\u201D]+$/g, '');
function scriptEdges(v) {
  if (!v || !v.utterances || !v.utterances.length) return {first: '', last: ''};
  const u = v.utterances;
  const hook = u.filter(x => x.in_hook_window);
  return {first: unquote((hook[0] || u[0]).text), last: unquote(u[u.length - 1].text), firstInWindow: !!hook.length};
}
function beats(v, hookText, question, cueQuote) {
  const e = scriptEdges(v);
  return h('div', {class: 'beats'},
    h('div', {class: 'beat'}, h('div', {class: 'eyebrow'}, e.firstInWindow ? 'Hook · first line, inside 3 s' : 'Hook · first line'),
      e.first ? h('q', {}, e.first) : null,
      cueQuote && unquote(cueQuote) !== e.first ? h('p', {}, h('b', {}, 'Cue cited by opener check: '), `\u201C${unquote(cueQuote)}\u201D`) : null,
      hookText ? h('p', {}, h('b', {}, 'Model\u2019s reading: '), hookText) : null),
    h('div', {class: 'beat end'}, h('div', {class: 'eyebrow'}, 'Cliffhanger · last line'),
      e.last ? h('q', {}, e.last) : null, question ? h('p', {}, h('b', {}, 'Open question (model): '), question) : null));
}

/* ---------- promos ---------- */
function viewPromos() {
  const recs = V().recommendations;
  const head = h('div', {class: 'section-head'}, h('div', {},
    h('p', {class: 'eyebrow'}, 'Ranked recommendations'), h('h2', {class: 'h-display'}, recs.length ? 'Post these first.' : 'Nothing cleared every gate.'),
    h('p', {class: 'lede'}, recs.length ? 'Ordered by the fixed model-score formula with explicit tie-breaks. Scores are model judgments from dialogue only; they do not predict views or retention.'
      : 'Promo Lab would rather return nothing than a cut it cannot support. The prospects are still here for an editor.')));
  if (!recs.length) {
    const nr = reviewFamilies().length;
    return h('div', {}, head, h('div', {class: 'empty'},
      h('div', {class: 'empty-icon'}, icon('empty')),
      h('div', {}, h('h3', {}, 'No cuts met every automatic editorial gate.'),
        h('p', {}, nr ? `Review flagged prospects or inspect the rejection reasons. ${plural(nr, 'event')} ${nr === 1 ? 'is' : 'are'} waiting in the review queue with the exact checks they missed.`
          : 'Inspect the rejection reasons to see which checks each cut failed, or compare the boundaries in the Cut Lab.'),
        h('div', {class: 'row'},
          nr ? h('button', {class: 'btn primary', onclick: () => go({tab: 'review'})}, 'Open review queue') : null,
          h('button', {class: 'btn', onclick: () => go({tab: 'excluded'})}, 'See rejection reasons'),
          h('button', {class: 'btn ghost', onclick: () => go({tab: 'lab'})}, 'Compare cuts')))));
  }
  return h('div', {}, head, h('div', {class: 'cards'}, recs.map((p, i) => {
    const fam = familyById(p.family_id);
    const v = fam ? fam.variants.find(x => x.candidate_id === p.promo_id) : null;
    const question = p.cliffhanger.text;
    const hookQuote = p.hook_quote || (v && v.opener && v.opener.cue_quote) || '';
    return h('article', {class: 'card pcard', style: {'--i': i}, 'aria-label': `Rank ${p.rank}`},
      h('div', {class: 'rank'}, h('span', {}, 'Rank'), h('b', {}, p.rank)),
      h('div', {class: 'body'},
        h('div', {class: 'pc-top'},
          h('div', {}, h('div', {class: 'row'}, chip(ep(p.episode_id), 'outline'), V().run.version === 'V1' ? chip('V1 rubric', 'outline') : chip(p.tier ? `Tier ${p.tier}` : 'Passed gates', 'teal'), chip('Human review pending', 'amber', true)),
            h('div', {class: 'tc', style: {'margin-top': '10px'}}, p.start_timecode, h('span', {class: 'arrow'}, '→'), p.end_timecode, h('span', {class: 'muted'}, `  ·  ${secs(p.duration_ms)}`))),
          h('div', {class: 'score', title: 'Weighted model score out of 100: 20% hook, 25% cliffhanger, 25% stands-alone, 10% each emotion, coherence, boundary.'}, h('b', {}, scoreText(p.score)), h('span', {}, '/ 100 model pts'))),
        miniTrack(p.episode_id, p.start_ms, p.end_ms),
        beats(v, p.hook_quote ? '' : p.hook.text, question, hookQuote),
        h('p', {class: 'why'}, h('b', {}, 'Why it works · '), p.why_it_works),
        p.boundary_summary ? h('p', {class: 'why small'}, h('b', {}, 'Why these edges · '), p.boundary_summary) : null,
        h('div', {class: 'flags'}, p.flags.map(f => h('span', {title: f.explanation}, chip(f.area === 'model_note' ? `Model note: ${f.explanation.slice(0, 70)}${f.explanation.length > 70 ? '…' : ''}` : f.label, f.severity === 'info' ? 'outline' : 'amber')))),
        h('div', {class: 'card-actions'},
          h('button', {class: 'btn', onclick: () => go({tab: 'lab', family: p.family_id, cut: p.promo_id})}, 'Inspect the boundary search →'),
          decisionControls(p.promo_id, p.family_id))));
  })));
}

/* ---------- review queue ---------- */
function viewReview() {
  const fams = reviewFamilies();
  const head = h('div', {class: 'section-head'}, h('div', {},
    h('p', {class: 'eyebrow'}, 'Human-in-the-loop'), h('h2', {class: 'h-display'}, 'Needs editorial review'),
    h('p', {class: 'lede'}, 'These events were not rejected, and not approved. Each shows the exact check it could not pass automatically. Decide, and the call is stored as editor input, never mixed into model output.')));
  if (!V().run.has_tiers) return h('div', {}, head, emptyBox('This run has no review tier.', 'Runs made under the V1 rubric only separate eligible from excluded cuts. Open a V2/V3/V4 run to see the review queue.'));
  if (!fams.length) return h('div', {}, head, emptyBox('The review queue is empty.', 'No event was routed to a human in this run. See the Excluded tab for what the checks rejected.', [h('button', {class: 'btn', onclick: () => go({tab: 'excluded'})}, 'See rejection reasons')]));
  return h('div', {}, head, h('div', {class: 'cards'}, fams.map((f, i) => {
    const v = representative(f);
    const nVar = f.variants.filter(x => x.status === 'review').length;
    const q = (v.ending && v.ending.question) || (v.unresolved_question && v.unresolved_question.text) || '';
    const issues = issueList(v.flags, false);
    return h('article', {class: 'card qcard', style: {'--i': i}},
      h('div', {class: 'pc-top'},
        h('div', {}, h('div', {class: 'row'}, statusChip('review'), chip(ep(f.episode_id), 'outline'), chip(`${nVar} of ${f.variants.length} nearby cuts`, 'outline')),
          h('div', {class: 'tc', style: {'margin-top': '10px'}}, tc(v.start_ms), h('span', {class: 'arrow'}, '→'), tc(v.end_ms), h('span', {class: 'muted'}, `  ·  ${secs(v.duration_ms)} · ${v.variant_label}`))),
        h('div', {class: 'score'}, h('b', {}, scoreText(v.score)), h('span', {}, '/ 100 model pts'))),
      f.proposal.event_summary ? h('p', {class: 'why'}, h('b', {}, 'Analyst summary (model text, unverified) · '), f.proposal.event_summary) : null,
      miniTrack(f.episode_id, v.start_ms, v.end_ms),
      beats(v, v.opener ? v.opener.reason : (v.criteria ? v.criteria.hook.reason : ''), q, v.opener && v.opener.cue_quote),
      h('div', {class: 'issues'}, h('div', {class: 'eyebrow'}, 'What an editor needs to check'),
        issues.length ? issues.map(x => h('div', {class: `issue ${x.severity}`}, h('div', {}, h('b', {}, x.label), ' — ', x.explanation))) : h('p', {class: 'muted small'}, 'No specific issue was recorded.')),
      h('div', {class: 'card-actions'},
        h('button', {class: 'btn', onclick: () => go({tab: 'lab', family: f.family_id, cut: v.candidate_id})}, 'Compare all cuts →'),
        decisionControls(v.candidate_id, f.family_id)));
  })));
}
function emptyBox(title, text, actions) {
  return h('div', {class: 'empty'}, h('div', {class: 'empty-icon'}, icon('check')),
    h('div', {}, h('h3', {}, title), h('p', {}, text), actions ? h('div', {class: 'row'}, actions) : null));
}

/* ---------- excluded ---------- */
function viewExcluded() {
  const fams = excludedFamilies();
  const rej = Object.entries(V().rejections || {});
  const head = h('div', {class: 'section-head'}, h('div', {},
    h('p', {class: 'eyebrow'}, 'Abstention, made visible'), h('h2', {class: 'h-display'}, 'What the checks caught'),
    h('p', {class: 'lede'}, 'Excluded cuts are kept with their reasons so an editor can disagree. Counts are per cut variant; one event can fail several checks.')));
  const cards = fams.map((f, i) => {
    const counts = {};
    for (const v of f.variants) for (const fl of v.flags) if (fl.severity === 'block') {
      const k = fl.label; (counts[k] ||= {n: 0, f: fl}).n++;
    }
    const v = representative(f);
    return h('article', {class: `card qcard ${f.status === 'failed' ? 'fail' : 'excl'}`, style: {'--i': i}},
      h('div', {class: 'pc-top'}, h('div', {class: 'row'}, statusChip(f.status), chip(ep(f.episode_id), 'outline'), chip(plural(f.variants.length, 'cut'), 'outline')),
        h('button', {class: 'btn ghost', onclick: () => go({tab: 'lab', family: f.family_id, cut: v.candidate_id})}, 'Inspect →')),
      f.proposal.event_summary ? h('p', {class: 'why'}, h('b', {}, 'Analyst summary (model text, unverified) · '), f.proposal.event_summary) : null,
      h('div', {class: 'issues'}, Object.values(counts).sort((a, b) => b.n - a.n).map(({n, f: fl}) =>
        h('div', {class: 'issue block'}, h('div', {}, h('b', {}, `${fl.label} ×${n}`), ' — ', fl.explanation)))));
  });
  const fails = V().failures;
  return h('div', {}, head,
    fams.length ? h('div', {class: 'cards'}, cards) : emptyBox('No event was excluded.', 'Every searched event either passed or was routed to review.'),
    rej.length ? h('section', {class: 'section'}, h('h3', {class: 'h3'}, 'Not shortlisted for boundary search'),
      h('p', {class: 'muted small'}, 'Events stopped before deep review (family-level reasons saved by the pipeline).'),
      h('div', {class: 'card panel bars', style: {'margin-top': 'var(--s3)'}}, rej.map(([reason, n]) => h('div', {class: 'barrow'}, h('span', {}, reason.replace(/_/g, ' ')), h('span', {class: 'n'}, n))))) : null,
    fails.length ? h('section', {class: 'section'}, h('h3', {class: 'h3'}, `Failed checks (${fails.length})`),
      h('p', {class: 'muted small'}, 'A failed check is never treated as a pass. Messages are copied from failures.jsonl.'),
      h('div', {class: 'card panel stack', style: {'margin-top': 'var(--s3)'}}, fails.slice(0, 30).map(x => h('div', {class: 'issue block'},
        h('div', {}, h('b', {}, `${x.stage || 'stage'} · `), h('span', {class: 'mono xs'}, x.candidate_id || x.episode_id || ''), h('div', {class: 'muted xs'}, x.error)))))) : null);
}

/* ---------- cut lab ---------- */
function viewLab(route) {
  const fams = families();
  if (!fams.length) return emptyBox('No events were searched in this run.', 'There is nothing to compare. Check coverage and failures on the Overview.');
  const order = s => ['recommended', 'accepted', 'eligible', 'review', 'excluded', 'failed'].indexOf(s);
  const sorted = [...fams].sort((a, b) => order(a.status) - order(b.status) || a.episode_id.localeCompare(b.episode_id));
  const f = familyById(route.family) || sorted[0];
  const sel = f.variants.find(v => v.candidate_id === route.cut) || f.variants.find(v => v.candidate_id === f.winner_id) || f.variants[0];
  const list = h('nav', {class: 'card events', 'aria-label': 'Searched events'}, h('h3', {class: 'eyebrow'}, `${plural(fams.length, 'searched event')}`),
    sorted.map(x => h('button', {class: 'ev', 'data-s': x.status, 'aria-current': String(x === f), onclick: () => go({family: x.family_id, cut: null})},
      h('span', {class: 'sd'}), h('span', {}, h('span', {class: 't'}, x.proposal.event_summary || x.family_id),
        h('span', {class: 'm'}, `${ep(x.episode_id)} · ${plural(x.variants.length, 'cut')} · ${SHORT[x.status] || x.status}`)))));
  return h('div', {class: 'lab'}, list, stage(f, sel));
}
function stage(f, sel) {
  const e = episodeById(f.episode_id);
  const vs = f.variants;
  let d0 = Math.min(...vs.map(v => v.start_ms)), d1 = Math.max(...vs.map(v => v.end_ms));
  d0 = Math.max(0, d0 - 3000); d1 = Math.min(e ? e.extent_ms : d1 + 3000, d1 + 3000);
  const span = d1 - d0, pct = ms => `${((ms - d0) / span * 100).toFixed(3)}%`;
  const wid = (a, b) => `${((b - a) / span * 100).toFixed(3)}%`;
  const maxTicks = window.innerWidth < 720 ? 4 : 9;
  const stepS = [1, 2, 5, 10, 15, 30, 60, 120].find(s => span / 1000 / s <= maxTicks) || 300;
  const ticks = [];
  for (let t = Math.ceil(d0 / 1000 / stepS) * stepS * 1000; t <= d1; t += stepS * 1000) ticks.push(t);
  const grid = () => ticks.map(t => h('span', {class: 'tl-grid', style: {left: pct(t)}}));
  const inCut = new Set(sel.utterances.map(u => u.source_block));
  const hookB = new Set(sel.utterances.filter(u => u.in_hook_window).map(u => u.source_block));
  const endEv = new Set([...(sel.ending ? sel.ending.evidence : []), ...((sel.unresolved_question || {}).evidence || [])]);
  const blocks = (e ? e.blocks : []).filter(b => b.end_ms > d0 && b.start_ms < d1);
  const scriptLane = h('div', {class: 'tl-lane'}, grid(), blocks.map(b => {
    const cls = b.kind === 'dialogue' ? `blk dlg${inCut.has(b.id) ? ' in' : ''}${hookB.has(b.id) ? ' hook' : ''}${endEv.has(b.id) && inCut.has(b.id) ? ' endb' : ''}` : 'blk oth';
    const a = Math.max(b.start_ms, d0), z = Math.min(b.end_ms, d1);
    return h('span', {class: cls, style: {left: pct(a), width: wid(a, z)}, title: `${tc(b.start_ms)}–${tc(b.end_ms)} ${b.kind}${b.speaker ? ' · ' + b.speaker : ''}${b.text ? ': ' + b.text : ''}`});
  }));
  const rows = vs.map(v => {
    const sc = v.score === null || v.score === undefined ? (v.reviewed ? '—' : 'no review') : scoreText(v.score);
    return h('button', {class: 'vrow', role: 'option', 'aria-selected': String(v === sel), id: `opt-${v.candidate_id}`,
      'aria-label': `${v.variant_label}, ${tc(v.start_ms)} to ${tc(v.end_ms)}, ${secs(v.duration_ms)}, ${(STATUS[v.status] || {}).label}${v.is_winner ? ', chosen cut' : ''}, score ${sc}`,
      tabindex: v === sel ? '0' : '-1', onclick: () => go({cut: v.candidate_id}),
      onkeydown: ev => {
        const i = vs.indexOf(v);
        if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
          ev.preventDefault();
          const n = vs[(i + (ev.key === 'ArrowDown' ? 1 : vs.length - 1)) % vs.length];
          go({cut: n.candidate_id}); setTimeout(() => document.getElementById(`opt-${n.candidate_id}`)?.focus(), 30);
        }
      }},
    h('div', {class: 'tl-row'}, h('span', {class: 'tl-name'}, v.variant_label, v.is_winner ? h('span', {style: {color: ['excluded', 'failed'].includes(v.status) ? 'var(--text-3)' : 'var(--teal-2)', display: 'block', 'font-weight': '600'}}, ['excluded', 'failed'].includes(v.status) ? '● top-ranked · excluded' : '● chosen') : null),
      h('div', {class: 'tl-lane'}, grid(),
        h('span', {class: 'vbar' + (v.is_winner ? ' win' : ''), 'data-s': v.status, style: {left: pct(v.start_ms), width: wid(v.start_ms, v.end_ms)}},
          h('span', {class: 'hw', style: {width: `${Math.min(100, 3000 / v.duration_ms * 100)}%`}}),
          h('span', {class: 'em'}), h('span', {class: 'sc'}, sc)))));
  });
  const dec = f.decision;
  const decLine = h('div', {class: 'decision-line' + (dec.tie_break_only ? ' tie' : '')},
    h('span', {class: 'eyebrow'}, 'Why this boundary'),
    h('div', {}, dec.summary || 'No saved decision.',
      dec.tie_break_only ? h('div', {style: {'margin-top': '8px'}}, chip('Tie-break only: no editorial difference between these edges', 'amber', true)) : null,
      dec.unreviewed_alternatives ? h('div', {class: 'xs muted', style: {'margin-top': '6px'}}, `${plural(dec.unreviewed_alternatives, 'nearby cut')} could not be reviewed, so ${dec.unreviewed_alternatives === 1 ? 'it was' : 'they were'} not compared.`) : null,
      f.pruned && f.pruned.length ? h('div', {class: 'xs muted', style: {'margin-top': '6px'}}, 'Not tried: ' + f.pruned.map(p => `${p.variant.replace(/_/g, ' ')} (${[].concat(p.reason).join(', ')})`).join('; ')) : null));
  const timeline = h('div', {class: 'timeline'},
    h('div', {class: 'tl-axis', 'aria-hidden': 'true'}, ticks.map(t => h('span', {class: 'tl-tick', style: {left: pct(t)}}, h('span', {}, tc(t))))),
    h('div', {class: 'tl-row tl-script', 'aria-hidden': 'true'}, h('span', {class: 'tl-name'}, 'Script', h('span', {class: 'muted', style: {display: 'block'}}, 'dialogue / action')), scriptLane),
    h('div', {role: 'listbox', 'aria-label': 'Compared cuts for this event. Use arrow keys to switch.'}, rows),
    h('div', {class: 'tl-legend'},
      h('span', {}, h('i', {class: 'sw', style: {background: 'rgba(45,212,191,.5)'}}), 'First 3 s (hook window)'),
      h('span', {}, h('i', {class: 'sw', style: {background: 'var(--coral)', width: '4px'}}), 'Out point'),
      h('span', {}, h('i', {class: 'sw', style: {background: 'rgba(242,180,65,.25)', border: '1px solid var(--amber)'}}), 'Needs review'),
      h('span', {}, h('i', {class: 'sw', style: {background: 'repeating-linear-gradient(135deg,#1C2634 0 3px,#151D29 3px 6px)', border: '1px solid var(--line-strong)'}}), 'Excluded'),
      h('span', {}, h('i', {class: 'sw', style: {border: '1px dashed var(--red)'}}), 'Review failed')));
  return h('section', {class: 'card stage', 'aria-label': 'Boundary comparison'},
    h('div', {class: 'stage-head'},
      h('div', {}, h('p', {class: 'eyebrow'}, `${ep(f.episode_id)} · ${f.family_id}`),
        h('h2', {class: 'h2', style: {'margin-top': '6px', 'max-width': '760px'}}, f.proposal.event_summary || 'Searched event'),
        h('p', {class: 'muted xs', style: {'margin-top': '6px'}}, 'Event summary is the analyst model’s own text and is not verified.'),
        auditLine(f)),
      statusChip(f.status)),
    decLine, timeline, inspector(f, sel));
}
function inspector(f, v) {
  const winner = f.variants.find(x => x.candidate_id === f.winner_id);
  const crit = v.criteria;
  const critBox = h('div', {class: 'box'}, h('h4', {}, h('span', {class: 'h3'}, 'Six judgments'), h('span', {class: 'muted xs'}, v.is_winner || !winner ? 'scores 0–4 · bar = gate' : 'Δ vs chosen cut')),
    crit ? h('div', {class: 'criteria'}, Object.entries(crit).map(([k, c]) => {
      const d = v.delta_vs_winner && v.delta_vs_winner[k] !== null && v.delta_vs_winner[k] !== undefined ? -v.delta_vs_winner[k] : null;
      const pass = c.score !== null && c.score >= c.gate;
      return [h('div', {class: 'crit'}, h('span', {class: 'nm'}, c.label),
        h('span', {class: `pips ${pass ? 'pass' : 'fail'}`, role: 'img', 'aria-label': `${c.label}: ${c.score ?? 'unknown'} of 4, gate ${c.gate}`},
          [1, 2, 3, 4].map(n => h('span', {class: 'pip' + (c.score !== null && n <= c.score ? ' on' : '')})),
          c.gate ? h('span', {class: 'gate', style: {left: `calc(${c.gate / 4 * 100}% - 1px)`}}) : null),
        h('span', {class: `dv ${d === null ? 'eq' : d > 0 ? 'up' : d < 0 ? 'down' : 'eq'}`}, v.is_winner || d === null ? (c.score ?? '?') + '/4' : (d > 0 ? '+' : d < 0 ? '−' : '±') + Math.abs(d))),
      h('p', {class: 'crit-reason'}, c.reason)];
    })) : h('p', {class: 'muted small'}, 'No valid full review was saved for this cut. It cannot qualify.'));

  const op = v.opener;
  const hookBox = h('div', {class: 'box hookbox'}, h('h4', {}, h('span', {class: 'h3'}, 'Opening'), h('span', {class: 'eyebrow'}, op ? 'isolated 3-second check' : 'full-cut reading')),
    op ? [h('div', {class: 'verdict'}, chip(op.hook_present ? 'Hook found' : 'No specific hook', op.hook_present ? 'teal' : 'red', true),
      op.cue_type && op.cue_type !== 'none' ? chip(op.cue_type, 'outline') : null, op.specificity !== undefined ? chip(`specificity ${op.specificity}`, 'outline') : null, op.uncertain ? chip('uncertain', 'amber') : null),
    op.cue_quote ? h('q', {class: 'quote'}, unquote(op.cue_quote)) : null, h('p', {class: 'small muted'}, op.reason)]
      : [h('p', {class: 'small'}, crit ? crit.hook.reason : 'No opener verdict saved.'), h('p', {class: 'xs muted'}, 'This run version had no separate isolated opener check.')]);
  const en = v.ending, uq = v.unresolved_question;
  const st = en && en.status;
  const stTone = st === 'unresolved' ? 'coral' : st === 'answered' || st === 'contradicted' ? 'red' : 'amber';
  const endBox = h('div', {class: 'box endbox'}, h('h4', {}, h('span', {class: 'h3'}, 'Ending'), h('span', {class: 'eyebrow'}, en ? 'separate ending check' : 'full-cut reading')),
    (uq || en) ? h('p', {class: 'small'}, h('b', {}, 'Proposed open question: '), (en && en.question) || (uq && uq.text)) : null,
    en ? [h('div', {class: 'verdict'}, chip(st ? `Question ${st}` : 'Not verified', stTone, true)),
      en.ending_quote ? h('q', {class: 'quote'}, unquote(en.ending_quote)) : null,
      en.answer_quote && st !== 'unresolved' ? h('p', {class: 'small'}, h('b', {}, 'Answered by: '), `“${unquote(en.answer_quote)}”`) : null,
      en.unresolved_consequence ? h('p', {class: 'small muted'}, en.unresolved_consequence) : null]
      : h('p', {class: 'xs muted'}, v.flags.some(x => x.code === 'ending_unknown') ? 'The ending check did not return a validated verdict for this cut.' : 'This run version had no separate ending check.'));
  const ctxBox = h('div', {class: 'box ctxbox full'}, h('h4', {}, h('span', {class: 'h3'}, 'Does it stand alone?'), v.essential_context_missing ? chip('Essential context missing', 'red', true) : (crit ? chip('No essential gap reported', 'teal', true) : null)),
    v.interpretation ? h('p', {class: 'small'}, h('b', {}, 'Reviewer’s reading: '), v.interpretation.text) : null,
    v.missing_context.length ? h('ul', {class: 'small muted', style: {margin: '6px 0 0', 'padding-left': '18px'}}, v.missing_context.map(m => h('li', {}, String(m).replace(/^\d+:\s*/, '')))) : null);

  const changeBox = (!v.is_winner && winner && (v.blocks_only_here.length || v.blocks_only_in_winner.length)) ? h('div', {class: 'box full'},
    h('h4', {}, h('span', {class: 'h3'}, 'What moving the edge changed'), h('span', {class: 'muted xs'}, 'compared with the chosen cut')),
    v.blocks_only_here.length ? [h('p', {class: 'eyebrow', style: {'margin-top': '8px'}}, 'Lines only in this cut'), v.blocks_only_here.map(id => srcLine(blockById(id), id))] : null,
    v.blocks_only_in_winner.length ? [h('p', {class: 'eyebrow', style: {'margin-top': '12px'}}, 'Lines only in the chosen cut'), v.blocks_only_in_winner.map(id => srcLine(blockById(id), id))] : null) : null;

  const issues = issueList(v.flags, true);
  const issueBox = h('div', {class: 'box'}, h('h4', {}, h('span', {class: 'h3'}, 'Checks and flags'), h('span', {class: 'muted xs'}, plural(issues.length, 'item'))),
    issues.length ? h('div', {class: 'issues', style: {'margin-top': '0'}}, issues.map(x => h('div', {class: `issue ${x.severity}`}, h('div', {}, h('b', {}, x.label), ' — ', x.explanation)))) : h('p', {class: 'muted small'}, 'No flags.'));

  const hookEv = new Set([...(crit ? crit.hook.evidence : [])]);
  const endEv = new Set([...(en ? en.evidence : []), ...((uq || {}).evidence || [])]);
  const transcript = h('div', {class: 'box full'}, h('h4', {}, h('span', {class: 'h3'}, 'What the restricted reviewer saw'), h('span', {class: 'muted xs'}, 'dialogue only · no names, camera, episode or analyst notes')),
    h('div', {class: 'transcript'}, v.utterances.map(u => h('div', {class: `utt${u.in_hook_window ? ' hookw' : ''}${endEv.has(u.source_block) ? ' cited-end' : hookEv.has(u.source_block) ? ' cited-hook' : ''}`},
      h('span', {class: 't'}, '+' + tc(u.start_ms)), h('span', {class: 'vc'}, u.voice || ''), h('span', {class: 'tx'}, u.text, h('span', {class: 'ref'}, u.id))))),
    h('p', {class: 'xs muted', style: {'margin-top': '8px'}}, 'Teal rows complete inside the first 3 s. A coral rule marks lines cited for the ending; a teal rule, lines cited for the hook.'));
  const srcBlocks = (episodeById(f.episode_id)?.blocks || []).filter(b => b.end_ms > v.start_ms && b.start_ms < v.end_ms);
  const source = V().source_included ? h('details', {class: 'disclosure full'}, h('summary', {}, `Original script in this interval (${srcBlocks.length} blocks, never sent to the reviewer)`),
    srcBlocks.map(b => srcLine(b, b.id))) : null;
  const raw = h('details', {class: 'disclosure full'}, h('summary', {}, 'Saved record (raw JSON)'), h('pre', {class: 'json'}, JSON.stringify(v.raw, null, 2)));

  return h('div', {},
    h('div', {class: 'insp-head'},
      h('div', {}, h('p', {class: 'eyebrow'}, v.is_winner ? (['excluded', 'failed'].includes(v.status) ? 'Top-ranked cut (excluded)' : 'Chosen cut') : 'Alternative cut'),
        h('div', {class: 'tc', style: {'margin-top': '6px', 'font-size': '18px'}}, `${v.variant_label} · `, tc(v.start_ms), h('span', {class: 'arrow'}, '→'), tc(v.end_ms), h('span', {class: 'muted'}, `  ·  ${secs(v.duration_ms)}`))),
      h('div', {class: 'row'}, statusChip(v.status), h('div', {class: 'score'}, h('b', {}, scoreText(v.score)), h('span', {}, '/ 100 model pts')))),
    h('div', {class: 'inspector'}, sourceBox(v), hookBox, endBox, ctxBox, changeBox, critBox, issueBox, transcript, source, raw,
      h('div', {class: 'full card-actions', style: {'border-top': '0', 'margin-top': '0'}}, decisionControls(v.candidate_id, f.family_id))));
}
/* ---------- Versioned source check ---------- */
const CLAIM_LABEL = {in_cut: 'in this cut', after_cut: 'after this cut', before_cut: 'before this cut', not_in_script: 'no matching episode dialogue', unquoted_description: 'prose, not checkable'};
function sourceCheckCard() {
  const s = V().source_check_summary;
  if (!s) return null;
  const ap = s.all_proposals || {};
  const disputed = (s.tier_changes || []).length;
  return h('div', {class: 'notice', style: {'grid-template-columns': 'auto 1fr'}},
    h('span', {class: 'glyph', style: {background: 'var(--surface-4)', color: 'var(--teal-2)'}}, `V${s.source_check_version || 5}`),
    h('div', {}, h('b', {}, 'Source check: model claims re-read against the script (0 model calls)'),
      h('span', {}, [ap.proposals ? `${ap.with_quote_outside_cut} of ${ap.proposals} analyst proposals quote a line outside their own cut; ` : '',
        `${(s.openings || {}).minimal || 0} of ${s.cut_variants} compared cuts open on a one- or two-word line; `,
        `${disputed} exclusion${disputed === 1 ? '' : 's'} disputed because the “answer” was a request, command or possibility. No cut was promoted to a recommendation.`].join(''))));
}
function auditLine(f) {
  const a = f.analyst_audit;
  if (!a) return null;
  const c = {}; for (const x of a.claims) c[x.status] = (c[x.status] || 0) + 1;
  const parts = Object.entries(c).map(([k, n]) => `${n} ${CLAIM_LABEL[k] || k}`);
  const bad = (c.not_in_script || 0) + (c.after_cut || 0) + (c.before_cut || 0);
  return h('p', {class: 'xs', style: {'margin-top': '4px', color: bad ? 'var(--amber)' : 'var(--text-3)'}}, 'Analyst’s quoted lines, checked against the script: ', parts.join(' · ') || 'none');
}
function sourceBox(v) {
  const s = v.source_check;
  if (!s) return null;
  const e = s.edges, ec = s.ending_check || {};
  const line = (l, label) => l ? h('div', {class: 'srcblk dialogue'}, h('span', {class: 'mono xs muted'}, '+' + tc(l.offset_ms)), h('span', {class: 'k'}, label), h('span', {}, `“${l.text}”`)) : h('p', {class: 'muted small'}, 'No spoken line.');
  return h('div', {class: 'box full', style: {'border-top': '2px solid var(--teal)'}},
    h('h4', {}, h('span', {class: 'h3'}, 'Source check'), h('span', {class: 'eyebrow'}, 'quoted from the script · 0 model calls')),
    s.original_tier && s.original_tier !== s.v5_tier ? h('div', {class: 'verdict'}, chip(`${s.original_tier} → ${s.v5_tier === 'B' ? 'needs review' : s.v5_tier}`, 'amber', true), h('span', {}, 'The saved exclusion reason is disputed by the source check. This is a review, not a recommendation.')) : null,
    line(e.opening, 'FIRST LINE'),
    h('div', {class: 'verdict'}, chip(e.opening_strength === 'substantive' ? 'Longer spoken opener (quality unverified)' : e.opening_strength === 'minimal' ? 'One- or two-word opener' : 'No spoken opener', e.opening_strength === 'substantive' ? 'teal' : 'amber', true),
      chip(e.opening_complete_in_3s ? 'Finishes inside 3 s' : 'Runs past 3 s', e.opening_complete_in_3s ? 'outline' : 'amber')),
    line(e.closing, 'LAST LINE'),
    ec.answer_speech_act ? h('p', {class: 'small muted'}, `Saved ending check said “answered”; the answer line is a ${ec.answer_speech_act.replace(/_/g, ' ')}.`) : null,
    s.claims_vs_this_cut && s.claims_vs_this_cut.some(c => c.quote) ? [h('p', {class: 'eyebrow', style: {'margin-top': '10px'}}, 'Analyst’s quotes vs this cut'),
      s.claims_vs_this_cut.filter(c => c.quote).map(c => h('div', {class: `issue ${c.status === 'in_cut' ? 'info' : c.status === 'not_in_script' ? 'block' : ''}`}, h('div', {}, h('b', {}, `“${c.quote}”`), ` (${c.field}): ${CLAIM_LABEL[c.status] || c.status}`)))] : null,
    s.repairs && s.repairs.length ? [h('p', {class: 'eyebrow', style: {'margin-top': '10px'}}, 'Suggested boundary repairs (not model-reviewed)'),
      s.repairs.map(r => h('div', {class: 'issue info'}, h('div', {}, h('b', {}, `${tc(r.start_ms)} → ${tc(r.end_ms)}`), ` — ${r.reason}`, (r.new_closing || r.new_opening) ? ` (“${(r.new_closing || r.new_opening).text}”)` : '')))] : null);
}
function srcLine(b, id) {
  if (!b) return h('div', {class: 'srcblk'}, h('span', {class: 't mono xs'}, ''), h('span', {class: 'k'}, ''), h('span', {class: 'mono xs'}, id));
  return h('div', {class: `srcblk ${b.kind}`}, h('span', {class: 'mono xs muted'}, tc(b.start_ms)),
    h('span', {class: 'k'}, (b.kind === 'dialogue' ? (b.speaker || 'SPEAKER') : b.label || b.kind).slice(0, 12)),
    h('span', {}, b.text !== undefined ? b.text : '(source text not included)'));
}

/* ---------- exports ---------- */
function csvCell(x) {
  let s = x === null || x === undefined ? '' : String(x);
  if (/^[=+\-@\t\r]/.test(s.trimStart())) s = "'" + s;
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], {type}));
  const a = h('a', {href: url, download: name}); document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function reviewQueueCSV() {
  const head = ['run_id', 'family_id', 'episode', 'candidate_id', 'variant', 'start', 'end', 'duration_s', 'status', 'tier', 'model_score', 'opening_cue', 'open_question', 'ending_quote', 'issues', 'editor_decision', 'editor_note', 'editor_recorded_utc'];
  const rows = [head];
  for (const f of families()) for (const v of f.variants) {
    if (!['review', 'recommended', 'accepted', 'eligible'].includes(v.status)) continue;
    const d = latestDecision(v.candidate_id);
    rows.push([V().run.run_id, f.family_id, f.episode_id, v.candidate_id, v.variant, tc(v.start_ms), tc(v.end_ms), (v.duration_ms / 1000).toFixed(3), v.status, v.tier || '', v.score ?? '',
      v.opener ? v.opener.cue_quote : '', (v.ending && v.ending.question) || (v.unresolved_question && v.unresolved_question.text) || '', v.ending ? v.ending.ending_quote : '',
      issueList(v.flags, false).map(x => x.label).join('; '), d ? d.decision : '', d ? d.note : '', d ? d.recorded_utc : '']);
  }
  return rows.map(r => r.map(csvCell).join(',')).join('\n') + '\n';
}
function decisionsCSV() {
  const head = ['recorded_utc', 'source', 'run_id', 'family_id', 'candidate_id', 'decision', 'note', 'editor'];
  return [head, ...state.decisions.map(d => head.map(k => d[k]))].map(r => r.map(csvCell).join(',')).join('\n') + '\n';
}
function viewExports() {
  const files = V().run.files || [];
  const desc = {
    'promos.json': 'Ranked recommendations with segments, hook, cliffhanger, evidence IDs and quality flags.',
    'promos.csv': 'The same ranked list as a spreadsheet.',
    'validation.json': 'Saved source re-validation of every exported interval.',
    'run_manifest.json': 'Config, code, prompt, model and runtime identities; per-call origin and timing.',
    'candidate_families.jsonl': 'Every compared cut, its restricted packet, saved review, tier and flags.',
    'failures.jsonl': 'Every failed check, kept explicitly.',
    'rejections.jsonl': 'Events not shortlisted, with reasons.',
    'report.html': 'The pipeline’s own static HTML report.',
    'v5_editor_briefs.csv': 'Source check: every cut in review with its real first and last lines, issues and unreviewed repair suggestions.',
    'v5_summary.json': 'Source check totals (zero model calls).',
  };
  const cards = files.map(n => h('div', {class: 'card xcard'}, h('span', {class: 'file'}, n), h('p', {}, desc[n] || 'Saved run file.'),
    h('a', {class: 'btn', href: fileUrl(n), download: `${V().run.id}-${n}`}, 'Download')));
  const run = V().run;
  const gen = [
    h('div', {class: 'card xcard'}, h('span', {class: 'file'}, 'review_queue.csv'), h('p', {}, 'Generated in your browser from the saved run: every cut that passed or needs review, with its issues and any editor decision. No scores are changed.'),
      h('button', {class: 'btn', onclick: () => download(`${run.id}-review_queue.csv`, reviewQueueCSV(), 'text/csv')}, 'Generate CSV')),
    h('div', {class: 'card xcard'}, h('span', {class: 'file'}, 'editor_decisions.csv'), h('p', {}, `${plural(state.decisions.length, 'editor decision')} recorded. Human input only, kept apart from model output${state.storage === 'server' ? '' : ' (this browser only)'}.`),
      h('button', {class: 'btn', disabled: state.decisions.length ? null : true, onclick: () => download(`${run.id}-editor_decisions.csv`, decisionsCSV(), 'text/csv')}, 'Download decisions')),
  ];
  const live = STATIC ? h('div', {class: 'card panel'}, h('h3', {class: 'h3'}, 'Analyze your own screenplay'),
    h('p', {class: 'muted small'}, 'This hosted page is a static, synthetic-only build: it shows genuine saved runs and cannot call a model. To analyze a new pack, run Promo Lab locally with a model server:'),
    h('pre', {class: 'json'}, 'pip install -r requirements.lock && pip install --no-deps -e .\nkuku studio-demo\nkuku serve --config private/local-config.json\n# open http://127.0.0.1:8765 → Run & export → upload a ZIP'))
    : liveRunPanel();
  return h('div', {},
    h('div', {class: 'section-head'}, h('div', {}, h('p', {class: 'eyebrow'}, 'Outputs'), h('h2', {class: 'h-display'}, 'Run & export'),
      h('p', {class: 'lede'}, 'Every file below is the run’s own unedited output. Nothing is regenerated or reformatted on download.'))),
    live,
    h('section', {class: 'section'}, h('h3', {class: 'h3'}, 'Saved run files'), h('div', {class: 'exports', style: {'margin-top': 'var(--s4)'}}, cards.length ? cards : h('p', {class: 'muted'}, 'No downloadable files.'))),
    h('section', {class: 'section'}, h('h3', {class: 'h3'}, 'Editorial worksheets'), h('div', {class: 'exports', style: {'margin-top': 'var(--s4)'}}, gen)));
}
function liveRunPanel() {
  const input = h('input', {type: 'file', accept: '.zip', 'aria-label': 'Screenplay ZIP'});
  const status = h('div', {class: 'progress', role: 'status'}, 'Ready. A local OpenAI-compatible model server must be running (see README → Start local inference).');
  const goBtn = h('button', {class: 'btn primary', onclick: async () => {
    const f = input.files[0];
    if (!f) { status.textContent = 'Select a compatible ZIP first.'; return; }
    goBtn.disabled = true;
    try {
      status.textContent = 'Uploading to this machine…';
      const r = await getJSON('/run', {method: 'POST', headers: {'Content-Type': 'application/zip', 'X-Run-Token': TOKEN}, body: f});
      const poll = async () => {
        const s = await getJSON('/status?id=' + r.id);
        status.textContent = s.message;
        if (s.state === 'running') setTimeout(poll, 1500);
        else {
          goBtn.disabled = false;
          if (s.state === 'finished') { state.runs = await listRuns(); renderRunPicker(); toast('Run finished — opening it'); go({run: s.key, tab: 'overview'}); }
        }
      };
      poll();
    } catch (e) { status.textContent = e.message; goBtn.disabled = false; }
  }}, 'Run analysis');
  return h('div', {class: 'card panel'}, h('h3', {class: 'h3'}, 'Analyze a new screenplay pack'),
    h('p', {class: 'muted small'}, 'Upload a ZIP of timed UTF-8 scripts. Everything runs on this machine; every episode is accounted for, and the new run appears in the run picker when it finishes. This can take several minutes per episode on a small model.'),
    h('div', {class: 'upload'}, h('label', {class: 'drop'}, 'Screenplay ZIP', input), goBtn), status);
}

/* ---------- render loop ---------- */
async function render() {
  const route = readRoute();
  const view = document.getElementById('view');
  try {
    if (!state.runs.length) {
      state.runs = await listRuns();
      if (!state.runs.length) {
        view.replaceChildren(emptyBox('No saved runs found.', 'Create the synthetic demo with `kuku studio-demo`, or point `kuku serve --runs` at a directory of saved runs.'));
        return;
      }
    }
    const key = state.runs.some(r => r.key === route.run) ? route.run : state.runs[0].key;
    if (key !== state.key || !state.view) {
      view.replaceChildren(h('div', {class: 'loading', role: 'status'}, h('span', {class: 'spinner', 'aria-hidden': 'true'}), 'Reading saved run…'));
      state.key = key; state.view = await loadRun(key); await loadDecisions();
      renderRunPicker(); renderRunMeta();
    }
    const tab = TABS.some(t => t.id === route.tab) ? route.tab : 'overview';
    renderTabs(tab);
    const builders = {overview: viewOverview, promos: viewPromos, review: viewReview, excluded: viewExcluded, lab: () => viewLab(route), exports: viewExports};
    view.replaceChildren(builders[tab]());
    document.title = `${TABS.find(t => t.id === tab).label} · ${V().run.label} · Kuku Promo Lab Studio`;
    if (state.lastTab !== tab) { window.scrollTo({top: 0}); state.lastTab = tab; }
  } catch (e) {
    view.replaceChildren(h('div', {class: 'error-box', role: 'alert'}, h('b', {}, 'Could not load the run. '), e.message));
  }
  const tb = document.querySelector('.topbar');
  document.documentElement.style.setProperty('--topbar-h', tb.offsetHeight + 'px');
}
window.addEventListener('hashchange', render);
window.addEventListener('resize', () => document.documentElement.style.setProperty('--topbar-h', document.querySelector('.topbar').offsetHeight + 'px'));
render();
})();
