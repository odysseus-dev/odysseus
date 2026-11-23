import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { parse } from 'acorn';

const root = fileURLToPath(new URL('../../', import.meta.url));
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'tests/native-ci-manifest.json'), 'utf8'));
function files(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const target = path.join(directory, entry.name);
    return entry.isDirectory() ? files(target) : /\.(?:mjs|cjs|js)$/.test(entry.name) ? [target] : [];
  });
}
function importsNativeTest(node) {
  if (!node || typeof node !== 'object') return false;
  if (node.type === 'ImportDeclaration' && node.source.value === 'node:test') return true;
  if (node.type === 'CallExpression' && node.callee.name === 'require' && node.arguments[0]?.value === 'node:test') return true;
  return Object.values(node).some(value => Array.isArray(value) ? value.some(importsNativeTest) : importsNativeTest(value));
}
const discovered = files(path.join(root, 'tests')).filter(file => {
  const source = fs.readFileSync(file, 'utf8');
  return source.includes('node:test') && importsNativeTest(parse(source, { ecmaVersion: 'latest', sourceType: 'module' }));
}).map(file => path.relative(root, file).split(path.sep).join('/')).sort();
const assigned = Object.values(manifest).flatMap(group => Array.isArray(group) ? group : Object.keys(group));
if (new Set(assigned).size !== assigned.length || JSON.stringify([...assigned].sort()) !== JSON.stringify(discovered)) {
  throw new Error('Native test coverage must be exhaustive and disjoint; update tests/native-ci-manifest.json for added, removed or duplicated files');
}
for (const wrappers of Object.values(manifest['python-ci'])) {
  for (const wrapper of wrappers) if (!fs.existsSync(path.join(root, wrapper))) throw new Error(`Missing pytest wrapper: ${wrapper}`);
}
if (process.argv.includes('--list')) {
  console.log(JSON.stringify({ discovered, runners: manifest }, null, 2));
} else {
  const reports = path.join(root, 'test-results/native-node');
  fs.mkdirSync(reports, { recursive: true });
  const result = spawnSync(process.execPath, ['--test', '--test-concurrency=1',
    '--test-reporter=spec', '--test-reporter-destination=stdout',
    '--test-reporter=junit', `--test-reporter-destination=${path.join(reports, 'junit.xml')}`,
    ...manifest['native-ci']], {
    cwd: root, stdio: 'inherit',
    env: { ...process.env, ENDPOINT_URL: 'http://127.0.0.1:1' },
  });
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
}
