/** Notes-owned document/window listeners. Detached editor/menu owners release their resources. */
export function createNotesLifecycle() {
    const owners = new Map();
    let observer = null;
    function release(owner) {
      const entry = owners.get(owner);
      if (!entry) return;
      owners.delete(owner);
      for (const remove of entry.removers) remove();
      if (!owners.size) { observer?.disconnect(); observer = null; }
    }
    function onDispose(owner, remove) {
      if (!owner) throw new Error('Notes listener needs a lifecycle owner');
      let entry = owners.get(owner);
      if (!entry) {
        entry = { connected: owner.isConnected, removers: [] };
        owners.set(owner, entry);
      }
      entry.removers.push(remove);
      if (!observer) {
        observer = new MutationObserver(() => {
          for (const [node, scope] of owners) {
            if (node.isConnected) scope.connected = true;
            else if (scope.connected) release(node);
          }
        });
        observer.observe(document.body, { childList: true, subtree: true });
      }
    }
    function listen(owner, target, event, listener, options) {
      onDispose(owner, () => target.removeEventListener(event, listener, options));
      target.addEventListener(event, listener, options);
    }
    function dispose() { for (const owner of [...owners.keys()]) release(owner); }
    return { listen, onDispose, release, dispose };
}
