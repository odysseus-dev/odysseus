import { resolve as resolvePath } from 'node:path';
import { pathToFileURL } from 'node:url';

const mockHostUrl = pathToFileURL(resolvePath('tests/helpers/document_email_host.mjs')).href;

export async function resolve(specifier, context, nextResolve) {
  if (
    specifier === './host.js'
    || /^(?:\.\.\/|\.\/)*host\.js$/.test(specifier)
  ) {
    const parent = context.parentURL || '';
    if (parent.includes('/frontend/src/features/documents/ui/')) {
      return { url: mockHostUrl, shortCircuit: true };
    }
  }
  return nextResolve(specifier, context);
}
