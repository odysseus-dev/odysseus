#!/usr/bin/env node
// Quick Edit benchmark v2 grader (gpt-5.4 vision). Reads the runner output
// (quickedit-bench.json) plus the saved before/after PNGs, scores each case
// 0-3, and writes quickedit-bench-grades.json with per-case scores and
// per-category averages. If an eye-scores JSON is provided (id->score), it
// merges the human (eye) score, averages eye+gpt, and flags disagreements.
//
// Usage:
//   OUT=/tmp/opencode/qe-bench \
//   EYE=/path/eye-scores.json \
//   node quickedit-bench-grade.js
const fs = require('node:fs');
const path = require('node:path');
const https = require('node:https');

const OUT = process.env.OUT || '/tmp/opencode/qe-bench';
const MANIFEST = process.env.MANIFEST || path.join(OUT, 'quickedit-bench.json');
const EYE = process.env.EYE || '';
const MODEL = process.env.GRADE_MODEL || 'gpt-5.4';
const KEY_FILE = process.env.OPENAI_KEY_FILE || '/home/pewds/.config/tiger/openai_user_20261009.key';

function getKey() {
  if (process.env.OPENAI_API_KEY) return process.env.OPENAI_API_KEY.trim();
  return fs.readFileSync(KEY_FILE, 'utf8').trim();
}
const KEY = getKey();
const URL = 'https://api.openai.com/v1/chat/completions';
function b64(p) { return fs.readFileSync(p).toString('base64'); }

function call(messages) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ model: MODEL, messages, max_completion_tokens: 2000 });
    const req = https.request(URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${KEY}`, 'Content-Length': Buffer.byteLength(body) },
    }, (res) => {
      let d = ''; res.on('data', (c) => (d += c));
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch (e) { reject(new Error('bad json: ' + d.slice(0, 300))); } });
    });
    req.on('error', reject);
    req.write(body); req.end();
  });
}

function rubric(prompt) {
  return [
    'You are a STRICT photo-editing grader. You are given the BEFORE image and the AFTER image.',
    `The user asked: "${prompt}".`,
    'Score the AFTER versus the BEFORE on an integer 0-3:',
    '0 = worse than the original, or broken/ugly/artifacts; 1 = did something but weak, irrelevant, or barely visible; 2 = decent, clearly visible and natural; 3 = what a good retoucher would produce.',
    'Judge harshly. If a face/eyes/lips/hair are smeared, distorted, blown out, plastic, or lost natural texture, score 0. If highlights are clipped or the image is washed out, cap at 1.',
    'Reply with ONLY JSON: {"score": <int>, "worse": <bool>, "artifacts": "<short>", "reason": "<short>"}.',
  ].join('\n');
}

(async () => {
  const manifest = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'));
  const eye = EYE && fs.existsSync(EYE) ? JSON.parse(fs.readFileSync(EYE, 'utf8')) : {};
  const out = [];
  for (const c of manifest.results) {
    const before = path.join(OUT, c.tag + '-before.png');
    const after = path.join(OUT, c.tag + '-after.png');
    let json = null, raw = null;
    if (fs.existsSync(before) && fs.existsSync(after)) {
      const content = [
        { type: 'text', text: rubric(c.prompt) },
        { type: 'text', text: 'BEFORE:' },
        { type: 'image_url', image_url: { url: 'data:image/png;base64,' + b64(before) } },
        { type: 'text', text: 'AFTER:' },
        { type: 'image_url', image_url: { url: 'data:image/png;base64,' + b64(after) } },
      ];
      for (let attempt = 0; attempt < 3 && !json; attempt++) {
        try {
          const res = await call([{ role: 'user', content }]);
          raw = res.choices && res.choices[0] && res.choices[0].message && res.choices[0].message.content;
          if (!raw && res.error) raw = 'API error: ' + JSON.stringify(res.error);
          const m = String(raw || '').match(/\{[\s\S]*\}/);
          if (m) { try { json = JSON.parse(m[0]); } catch (_) {} }
        } catch (e) { raw = String(e.message || e); }
      }
    } else {
      raw = 'missing before/after images';
    }
    const gpt = json && typeof json.score === 'number' ? json.score : null;
    const eyeScore = typeof eye[c.id] === 'number' ? eye[c.id] : null;
    const combined = (gpt != null && eyeScore != null) ? (gpt + eyeScore) / 2 : (gpt != null ? gpt : eyeScore);
    out.push({ id: c.id, cat: c.cat, prompt: c.prompt, ms: c.ms, ok: c.ok, gpt, eye: eyeScore, avg: combined, disagree: (gpt != null && eyeScore != null && Math.abs(gpt - eyeScore) >= 2), artifacts: json && json.artifacts, reason: json && json.reason });
    console.log(`GRADE #${c.id} [${c.cat}] gpt=${gpt} eye=${eyeScore} avg=${combined} ${c.prompt}`);
  }

  const byCat = {};
  for (const r of out) {
    if (typeof r.avg !== 'number') continue;
    (byCat[r.cat] = byCat[r.cat] || []).push(r.avg);
  }
  const cats = {};
  for (const [k, v] of Object.entries(byCat)) cats[k] = +(v.reduce((a, b) => a + b, 0) / v.length).toFixed(2);
  const scored = out.filter(r => typeof r.avg === 'number');
  const overall = scored.length ? +(scored.reduce((a, b) => a + b.avg, 0) / scored.length).toFixed(2) : null;
  const disagreements = out.filter(r => r.disagree).map(r => r.id);

  fs.writeFileSync(path.join(OUT, 'quickedit-bench-grades.json'), JSON.stringify({ model: MODEL, overall, byCat, disagreements, results: out }, null, 2));
  console.log(`BENCH_GRADES overall=${overall}/3 byCat=${JSON.stringify(cats)} disagreements=${JSON.stringify(disagreements)} -> ${path.join(OUT, 'quickedit-bench-grades.json')}`);
})().catch((e) => { console.error('FAIL', e); process.exit(1); });
