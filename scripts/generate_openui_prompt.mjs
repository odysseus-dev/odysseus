import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { openuiLibrary, openuiPromptOptions } from '@openuidev/react-ui/genui-lib';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const outPath = resolve(root, 'config/openui_system_prompt.txt');

const prompt = openuiLibrary.prompt({
  ...openuiPromptOptions,
  additionalRules: [
    ...(openuiPromptOptions.additionalRules || []),
    'Make the first viewport visually strong enough for a short X video.',
    'Prefer dashboards, controls, charts, forms, tables, timelines, and stateful product surfaces over static marketing copy.',
    'Do not explain the UI in prose. Return valid openui-lang only.',
  ],
});

await mkdir(dirname(outPath), { recursive: true });
await writeFile(outPath, prompt, 'utf8');
console.log(`Wrote ${outPath} (${prompt.length} chars)`);
