/**
 * Batch runner — a small, dependency-free orchestrator that applies one action
 * to every item in a list (open documents), isolating each item so a failure on
 * one never aborts the batch. Pure async control flow: the caller supplies
 * `runOne`, so the same loop is unit-testable without an editor.
 */

/**
 * @param {object} options
 * @param {Array<{id:string,name?:string}>} options.documents
 * @param {(doc:object, index:number)=>Promise<any>} options.runOne
 * @param {number} [options.delayMs]
 * @param {(done:number,total:number,doc:object)=>void} [options.onProgress]
 * @returns {Promise<Array<{id,name,ok,output?,error?}>>}
 */
export async function runBatchOverDocuments({ documents, runOne, delayMs = 0, onProgress = null } = {}) {
  const list = Array.isArray(documents) ? documents.filter(Boolean) : [];
  const results = [];
  for (let index = 0; index < list.length; index += 1) {
    const doc = list[index];
    try {
      const output = await runOne(doc, index);
      results.push({ id: doc.id, name: doc.name, ok: true, output: output === undefined ? null : output });
    } catch (error) {
      results.push({ id: doc.id, name: doc.name, ok: false, error: String((error && error.message) || error) });
    }
    if (typeof onProgress === 'function') {
      try { onProgress(index + 1, list.length, doc); } catch { /* progress is best-effort */ }
    }
    if (delayMs > 0) await new Promise(resolve => setTimeout(resolve, delayMs));
  }
  return results;
}

export default { runBatchOverDocuments };
