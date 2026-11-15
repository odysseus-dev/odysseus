import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
test('documentFlows extracted behavior', () => {
 const suite=fileURLToPath(new URL('./documentFlows.behavior.mjs',import.meta.url)); const env={...process.env}; delete env.NODE_TEST_CONTEXT; delete env.NODE_TEST_WORKER_ID;
 const result=spawnSync(process.execPath,['--experimental-vm-modules','--test',suite],{encoding:'utf8',env});
 if(result.status!==0) process.stderr.write(result.stderr||result.stdout||'');
 assert.equal(result.status,0,result.stderr||result.stdout); assert.match(result.stdout||'',/ℹ pass [1-9]/);
});
