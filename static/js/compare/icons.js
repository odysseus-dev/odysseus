// compare/icons.js — SVG icons, prompt templates, and constants

// ── SVG Icons ──

export const EYE_OPEN = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
export const EYE_CLOSED = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><line x1="8" y1="16" x2="16" y2="8"/><line x1="8" y1="8" x2="16" y2="16"/></svg>';
export const SAVE_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>';
export const CHAT_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
export const ICON_COPY = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
export const ICON_REROLL = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>';
export const ICON_EXPAND = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>';
export const ICON_COLLAPSE = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/></svg>';
export const ICON_DICE = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="20" rx="3"/><circle cx="8" cy="8" r="1.5" fill="currentColor"/><circle cx="16" cy="8" r="1.5" fill="currentColor"/><circle cx="8" cy="16" r="1.5" fill="currentColor"/><circle cx="16" cy="16" r="1.5" fill="currentColor"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/></svg>';
export const ICON_PLAY = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5,3 19,12 5,21"/></svg>';
export const ICON_CODE = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>';
export const ICON_CLOSE = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
// Parallel = lines side by side, Sequential = numbered list
export const ICON_PARALLEL = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="20" y2="18"/></svg>';
export const ICON_SEQUENTIAL = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="20" y2="6"/><line x1="8" y1="12" x2="20" y2="12"/><line x1="8" y1="18" x2="20" y2="18"/><circle cx="4" cy="6" r="1.5" fill="currentColor"/><circle cx="4" cy="12" r="1.5" fill="currentColor"/><circle cx="4" cy="18" r="1.5" fill="currentColor"/></svg>';
export const SEND_SVG = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';

// ── Animation ──

export const WAVE_FRAMES = ['▁▂▃', '▂▃▄', '▃▄▅', '▄▅▆', '▅▆▇', '▆▅▄', '▅▄▃', '▄▃▂'];

// ── Storage keys & limits ──

export const VOTES_STORAGE_KEY = 'odysseus-compare-votes';
export const VOTES_MAX = 200;
export const POOL_STORAGE_KEY = 'odysseus-shuffle-pool-excluded';

// ── Evaluation prompt templates ──
//
// Five high-signal prompts per category — each picked to differentiate models
// on a distinct capability. The Visual / SVG-render prompt in `chat` ends with
// the subject as the last words, so swapping "a pelican riding a bicycle" for
// anything else is a one-line edit.

export const EVAL_PROMPTS = {
  chat: [
    // ── ★ Featured — prompts that have actually broken frontier models ──
    { sub: '★ Featured', label: 'Sum digits 2^100', answer: '115', prompt: 'Compute the sum of the decimal digits of 2^100. Do NOT use code execution — work it out by reasoning about the number. Show every step, then end with the final number on its own line.' },
    { sub: '★ Featured', label: 'Three jugs',       answer: '2 pours: 7→5, 7→3', prompt: 'You have three jugs of capacities 7, 5, and 3 liters. The 7-liter jug starts full; the others empty. Using only pouring (no markings), produce the shortest sequence of pours that leaves exactly 2 liters in the 3-liter jug. Output each step as `pour A → B` on its own line. Then state the total number of pours on a final line.' },

    { sub: 'Visual',         label: 'Draw SVG',         prompt: 'Output a complete self-contained HTML file (```html block, no explanation, no other text) that centers a single SVG illustration on a simple background. The SVG must use only inline shapes — no <img>, no external assets, no JavaScript. Make it expressive and detailed. The SVG should depict: a friendly robot' },
    { sub: 'Visual explain', label: 'Black hole HTML',  prompt: 'Output a complete HTML file (```html block, no explanation outside the code) that visually explains how a black hole forms. Use four labeled "frames" laid out left-to-right (or stacked on small screens) showing: 1) a glowing massive star, 2) the star going supernova with shockwave rings, 3) collapse into a singularity, 4) the final black hole with a curved accretion disk and bent light around it. Use only vanilla HTML, CSS, and inline SVG — no JavaScript, no images. Each frame should have a one-sentence caption.' },
    { sub: 'Visual explain', label: 'Butterfly ASCII',  prompt: 'Explain the butterfly lifecycle using ASCII art. Produce four separate frames in fenced code blocks, in order: egg, caterpillar, chrysalis, adult butterfly. Each frame must be drawn with monospace ASCII characters only and be visually recognizable as the creature/stage. Below each frame add one playful one-line caption (no longer than 15 words) describing what is happening at that stage.' },
  ],
  code: [
    { sub: 'Algorithms',   label: 'LRU cache',       prompt: 'Implement an LRU cache with O(1) get and put operations. Support a configurable max capacity. Write it in any language with full comments.' },
    { sub: 'Debugging',    label: 'Race condition',  prompt: 'This Go code has a race condition. Find it, explain why it happens, and fix it:\n\nvar counter int\nfunc increment(wg *sync.WaitGroup) {\n    defer wg.Done()\n    for i := 0; i < 1000; i++ {\n        counter++\n    }\n}' },
    { sub: 'Debugging',    label: 'Security review', prompt: 'Review this code for bugs, security issues, and performance problems:\n\napp.get("/user/:id", (req, res) => {\n  const query = `SELECT * FROM users WHERE id = ${req.params.id}`;\n  db.query(query, (err, result) => {\n    res.json(result[0]);\n  });\n});' },
    { sub: 'Architecture', label: 'URL shortener',   prompt: 'Design a URL shortener service. Cover the API, database schema, and how you would handle 1000 requests per second.' },
    { sub: 'Refactoring',  label: 'Clean up',        prompt: 'Refactor this code to be more idiomatic and efficient:\n\nresults = []\nfor i in range(len(data)):\n    if data[i]["status"] == "active":\n        if data[i]["score"] > 50:\n            results.append(data[i]["name"].upper())' },
  ],
  agent: [
    { sub: 'Web tasks',  label: 'Multi-step',     prompt: 'Search the web for the current population of the 3 largest cities in the world, then calculate what percentage of the world\'s total population lives in those cities.', toggles: ['web'] },
    { sub: 'Web tasks',  label: 'Fact check',     prompt: 'Fact-check these claims: 1) The Great Wall of China is visible from space. 2) Humans only use 10% of their brains. 3) Lightning never strikes the same place twice. Cite sources.', toggles: ['web'] },
    { sub: 'Web tasks',  label: 'Compare prices', prompt: 'Find and compare the pricing, features, and limitations of the top 3 cloud GPU providers for machine learning training. Create a markdown comparison table.', toggles: ['web'] },
    { sub: 'Code tasks', label: 'Script + run',   prompt: 'Write a Python script that generates a bar chart of the 5 most common programming languages in 2025 and save it as chart.png. Then run it.' },
    { sub: 'Math',       label: 'Proof + verify', prompt: 'Prove that the square root of 2 is irrational. Then write a Python program that approximates it using Newton\'s method to 50 decimal places and verify.' },
  ],
  html: [
    { sub: 'Games',      label: 'Snake',         prompt: 'Output a complete HTML file (```html block) for a Snake game. ONLY use vanilla HTML, CSS, and JavaScript — no libraries, no Python, no imports, no external files. Canvas-based, neon green snake on dark grid, glowing food, score counter, speed increases, game over + restart. Skip any explanation, just output the code.' },
    { sub: 'Games',      label: 'Breakout',      prompt: 'Output a complete HTML file (```html block) for a Breakout brick breaker game. ONLY use vanilla HTML, CSS, and JavaScript — no libraries, no Python, no imports, no external files. Canvas-based, colorful gradient brick rows, glowing paddle, ball with trail, score + lives, particle explosions on break. Skip any explanation, just output the code.' },
    { sub: 'Animation',  label: 'Solar system',  prompt: 'Output a complete HTML file (```html block) for an animated solar system. ONLY use vanilla HTML, CSS, and JavaScript — no libraries, no Python, no imports, no external files. Canvas-based, glowing Sun center, 8 planets orbiting at correct relative speeds with real colors, orbit trails, starfield background, labels on hover. Skip any explanation, just output the code.' },
    { sub: 'Animation',  label: 'Matrix rain',   prompt: 'Output a complete HTML file (```html block) for the Matrix digital rain effect. ONLY use vanilla HTML, CSS, and JavaScript — no libraries, no Python, no imports, no external files. Full-screen canvas, green katakana characters falling at varying speeds, glowing heads, fading trails, scan-line overlay. Skip any explanation, just output the code.' },
    { sub: 'Generative', label: 'Fractal tree',  prompt: 'Output a complete HTML file (```html block) for an interactive fractal tree. ONLY use vanilla HTML, CSS, and JavaScript — no libraries, no Python, no imports, no external files. Canvas-based, tree grows from bottom with recursive branches, sliders for angle/depth/length/wind, gradient colors from brown trunk to green leaves. Skip any explanation, just output the code.' },
  ],
  search: [
    { sub: 'Factual',    label: 'Current events', prompt: 'latest AI regulation news 2025' },
    { sub: 'Technical',  label: 'Programming',    prompt: 'Rust vs Go performance benchmarks 2025' },
    { sub: 'Research',   label: 'Academic',       prompt: 'transformer architecture improvements since attention is all you need' },
    { sub: 'Comparison', label: 'GPU providers',  prompt: 'cloud GPU providers pricing comparison 2025' },
    { sub: 'Factual',    label: 'Science',        prompt: 'CRISPR gene therapy breakthroughs' },
  ],
};
