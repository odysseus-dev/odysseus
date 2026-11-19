import path from 'node:path';
import { pathToFileURL } from 'node:url';

const repoRoot = path.resolve(process.cwd());

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith('/static/')) {
    const parsed = new URL(specifier, 'https://odysseus.invalid');
    const resolved = pathToFileURL(path.resolve(repoRoot, parsed.pathname.slice(1))).href + parsed.search + parsed.hash;
    return { url: resolved, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}
