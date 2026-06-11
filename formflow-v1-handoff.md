# FormFlow — V1 Handoff Doc
> Claude Code build spec · drafted June 2026

---

## What We're Building

**FormFlow** is a universal form renderer that turns any question set into a focused, one-question-at-a-time workspace — think Typeform but you bring the form. The user pastes text, drops a file, or provides a URL and FormFlow parses it into a navigable, tappable UI with live character/word limits where specified.

This is a single-page React app. No backend. Claude API handles all parsing.

---

## Repo Setup

```bash
# Scaffold
npm create vite@latest formflow -- --template react-ts
cd formflow
npm install

# Dependencies
npm install @anthropic-ai/sdk        # Claude API client
npm install react-dropzone           # file upload drag-and-drop
npm install pdfjs-dist               # PDF text extraction (client-side)
npm install mammoth                  # .docx text extraction (not needed v1 but good to have)
npm install clsx                     # conditional classnames
```

**Node version:** 18+ required.

---

## Project Structure

```
formflow/
├── src/
│   ├── App.tsx                  # Root — manages global state + screen routing
│   ├── main.tsx
│   ├── components/
│   │   ├── InputScreen.tsx      # Step 1: accept input (paste / upload / URL)
│   │   ├── ParseLoader.tsx      # Step 2: "Parsing your form..." loading state
│   │   ├── QuestionCard.tsx     # Step 3: one-question-at-a-time UI
│   │   ├── ReviewScreen.tsx     # Step 4: all answers before export
│   │   ├── ExportBar.tsx        # Copy / download answers
│   │   └── ProgressBar.tsx      # Thin top bar showing question N of total
│   ├── lib/
│   │   ├── parseForm.ts         # Claude API call → structured question list
│   │   ├── extractText.ts       # File → raw string (PDF, MD, HTML, image, plain text)
│   │   └── types.ts             # Shared TypeScript types
│   └── styles/
│       └── globals.css          # CSS variables + base styles
├── .env.local                   # ANTHROPIC_API_KEY (never commit)
├── vite.config.ts
└── README.md
```

---

## Core Types

```typescript
// src/lib/types.ts

export type QuestionType =
  | 'text'           // short single-line answer
  | 'textarea'       // long-form answer
  | 'choice'         // pick one from options
  | 'multi'          // pick multiple
  | 'yesno'          // binary
  | 'scale'          // numeric 1–N rating
  | 'email'
  | 'number';

export interface Question {
  id: string;
  type: QuestionType;
  label: string;            // the question text
  required: boolean;
  options?: string[];       // for choice / multi
  scaleMin?: number;        // for scale
  scaleMax?: number;
  wordLimit?: number;       // optional — only set when source specifies it
  charLimit?: number;       // optional — only set when source specifies it
  placeholder?: string;
}

export interface Answer {
  questionId: string;
  value: string | string[] | number;
}

export type AppScreen = 'input' | 'loading' | 'form' | 'review';
```

---

## Step 1 — Input Screen (`InputScreen.tsx`)

Three modes in one screen. User only needs one:

| Mode | UI | Accepted formats |
|---|---|---|
| Paste | `<textarea>` | Plain text, Markdown, HTML |
| Upload | Drag-and-drop zone | `.pdf`, `.md`, `.txt`, `.html`, `.png`, `.jpg`, `.jpeg` |
| URL | Text input + fetch button | Any public URL (HTML page) |

**On submit:** call `extractText()` → get raw string → pass to `parseForm()`.

---

## Step 2 — Text Extraction (`extractText.ts`)

```typescript
// src/lib/extractText.ts

export async function extractText(input: File | string): Promise<string> {
  // If string → check if it looks like a URL → fetch it
  // If File:
  //   .pdf  → pdfjs-dist: load doc, iterate pages, concat textContent
  //   .png/.jpg/.jpeg → convert to base64, send to Claude vision (see parseForm)
  //   .md / .txt / .html → file.text()
}
```

**Image files are a special case:** don't extract text. Instead pass the base64 image directly to the Claude API call in `parseForm.ts` as a vision input alongside the parse prompt. Flag this with `isImage: true`.

---

## Step 3 — Claude Parse Call (`parseForm.ts`)

This is the core intelligence. Call `claude-sonnet-4-20250514` with a structured prompt.

```typescript
// src/lib/parseForm.ts

import Anthropic from '@anthropic-ai/sdk';
import { Question } from './types';

const client = new Anthropic({
  apiKey: import.meta.env.VITE_ANTHROPIC_API_KEY,
  dangerouslyAllowBrowser: true,
});

const SYSTEM_PROMPT = `
You are a form parser. Given raw text (or an image) from a form, application, questionnaire, or survey, extract every question and return a JSON array of question objects.

Rules:
- Only set wordLimit or charLimit if the source EXPLICITLY states a limit (e.g. "max 200 words", "250 characters").
- Infer the best question type from context. Default to "textarea" for open-ended answers.
- Set required: true unless the source says "optional" or "if applicable".
- For multiple choice, extract each option into the options array.
- Return ONLY the JSON array. No explanation. No markdown fences.

JSON shape per question:
{
  "id": "q1",
  "type": "textarea",
  "label": "What is your greatest professional achievement?",
  "required": true,
  "wordLimit": 150,    // only if explicitly stated
  "charLimit": null,   // only if explicitly stated
  "options": [],       // only for choice/multi
  "placeholder": ""
}
`;

export async function parseForm(rawText: string, imageBase64?: string): Promise<Question[]> {
  const userContent: Anthropic.MessageParam['content'] = imageBase64
    ? [
        { type: 'image', source: { type: 'base64', media_type: 'image/jpeg', data: imageBase64 } },
        { type: 'text', text: 'Extract all questions from this form image.' },
      ]
    : rawText;

  const response = await client.messages.create({
    model: 'claude-sonnet-4-20250514',
    max_tokens: 4096,
    system: SYSTEM_PROMPT,
    messages: [{ role: 'user', content: userContent }],
  });

  const text = response.content
    .filter(b => b.type === 'text')
    .map(b => b.text)
    .join('');

  const cleaned = text.replace(/```json|```/g, '').trim();
  const parsed: Question[] = JSON.parse(cleaned);

  // Assign stable IDs
  return parsed.map((q, i) => ({ ...q, id: `q${i + 1}` }));
}
```

---

## Step 4 — Question Card (`QuestionCard.tsx`)

One question fills the screen. Render input type based on `question.type`.

### Character / Word Limit Counter

Only render the counter **when `wordLimit` or `charLimit` is present on the question.**

```typescript
// Count logic
const wordCount = value.trim().split(/\s+/).filter(Boolean).length;
const charCount = value.length;

const isOverWordLimit = question.wordLimit ? wordCount > question.wordLimit : false;
const isOverCharLimit = question.charLimit ? charCount > question.charLimit : false;
const isOver = isOverWordLimit || isOverCharLimit;
```

Counter display states:
- Default: neutral gray — `"142 / 200 words"`
- Warning (>85% used): amber
- Over limit: red + disable "Next" button

### Navigation

- **Back** / **Next** buttons
- **Enter** key advances (except textarea — use Shift+Enter for newline, Enter for next)
- Clicking "Next" on the last question → go to Review screen

---

## Step 5 — Review Screen (`ReviewScreen.tsx`)

Table or card list of every Q + A. Two actions:

- **Copy all answers** → formatted plain text to clipboard
- **Download .txt** → same content as a file

No submission endpoint in V1. Export only.

---

## App State (in `App.tsx`)

```typescript
const [screen, setScreen] = useState<AppScreen>('input');
const [rawText, setRawText] = useState('');
const [questions, setQuestions] = useState<Question[]>([]);
const [answers, setAnswers] = useState<Answer[]>([]);
const [currentIndex, setCurrentIndex] = useState(0);
const [error, setError] = useState<string | null>(null);
```

Flow:
```
input  →  (extractText + parseForm)  →  loading  →  form  →  review
```

---

## Environment

```bash
# .env.local
VITE_ANTHROPIC_API_KEY=sk-ant-...
```

> ⚠️ `dangerouslyAllowBrowser: true` is set on the Anthropic client. This is fine for local dev. For any public deployment, proxy the API call through a server route so the key is never exposed.

---

## Design Tokens

```css
/* src/styles/globals.css */
:root {
  --bg: #0f0f0f;
  --surface: #1a1a1a;
  --border: #2a2a2a;
  --text-primary: #f0f0f0;
  --text-secondary: #888;
  --accent: #e8a87c;       /* warm amber — single signature color */
  --danger: #e05c5c;
  --warning: #e8c87c;
  --mono: 'JetBrains Mono', monospace;   /* for counters */
  --sans: 'Inter', system-ui, sans-serif;
  --radius: 12px;
  --radius-sm: 6px;
}
```

One-question-at-a-time layout: question label large (`2rem`), input below, counter bottom-right of input, nav buttons bottom of viewport. Progress bar fixed top.

---

## V1 Acceptance Criteria

- [ ] User can paste text and get a parsed form
- [ ] User can upload a PDF and get a parsed form
- [ ] User can upload a PNG/JPEG and get a parsed form
- [ ] User can upload a `.md` or `.html` file and get a parsed form
- [ ] Questions display one at a time with back/next navigation
- [ ] Word limit counter appears **only** when the source specifies a limit
- [ ] Counter turns red and Next is disabled when over limit
- [ ] Review screen shows all Q&A
- [ ] Copy to clipboard works
- [ ] Download .txt works
- [ ] Keyboard navigation works (Enter to advance, Shift+Enter for newlines)

---

## Out of Scope (V2+)

- URL fetch (needs server-side proxy to avoid CORS)
- Save/resume sessions (localStorage or Supabase)
- Conditional branching / skip logic
- Submit to external endpoint
- Auth / user accounts
- Mobile-native gestures

---

## Known Gotchas

- **PDF.js worker:** needs to be configured explicitly in Vite. Add to `vite.config.ts`:
  ```typescript
  optimizeDeps: { include: ['pdfjs-dist'] }
  ```
  And set `pdfjsLib.GlobalWorkerOptions.workerSrc` before use.

- **Claude API in browser:** `dangerouslyAllowBrowser: true` suppresses the SDK warning. Fine locally, not for production.

- **Image parsing:** vision input works best with clear, high-contrast form scans. Noisy backgrounds or handwriting will degrade parse quality — flag to user if confidence seems low.

- **Malformed JSON from Claude:** wrap `JSON.parse` in try/catch and retry once with a stricter prompt if it fails. Add a user-facing error state on second failure.

---

*Built with: React + TypeScript + Vite + Anthropic SDK · No backend required for V1*
