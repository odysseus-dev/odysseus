"""Mutation invalidation coverage for cached visual Markdown serialization."""

from tests.test_document_visual_editor import _run_browser


def test_visual_serialization_cache_invalidates_live_mutations_and_lifecycle_changes():
    data = _run_browser(r"""
      await boot('visual-cache-invalidation');
      await open({ id: 'cache-md', title: 'Cache', language: 'markdown', current_content: 'Seed' });
      const result = await page.evaluate(async () => {
        const sync = await import('/static/js/document/ui/richEditor/markdownVisualSync.js');
        const codec = await import('/static/js/document/domain/markdownVisualCodec.js');
        const root = document.getElementById('doc-email-richbody');
        const oracle = el => codec.serializeVisualNodesWithFidelity(sync.domToVisualNodes(el));
        const cached = el => sync.visualRichToMarkdownWithFidelity(el);
        const matchesOracle = el => {
          const actual = cached(el);
          const expected = oracle(el);
          return JSON.stringify(actual) === JSON.stringify(expected);
        };
        const checks = {};

        // Warm the block cache, then perform character, nested formatting,
        // list/check state and table/cell edits in the same task. The cached
        // path must consume pending MutationObserver records synchronously.
        root.innerHTML = '<p>Alpha</p><ul><li data-checked="false">Task</li></ul><table><tbody><tr><td>A</td><td>B</td></tr></tbody></table>';
        cached(root);
        const paragraphText = root.querySelector('p').firstChild;
        paragraphText.nodeValue = 'Changed';
        const strong = document.createElement('strong');
        strong.textContent = ' bold';
        root.querySelector('p').append(strong);
        const item = root.querySelector('li');
        item.setAttribute('data-checked', 'true');
        item.classList.add('checked');
        root.querySelector('td').textContent = 'Cell edit';
        root.querySelector('table').setAttribute('data-alert', 'warning');
        checks.sameTickEdits = matchesOracle(root);

        // Root insertion and deletion must invalidate all affected blocks.
        cached(root);
        const inserted = document.createElement('h2');
        inserted.textContent = 'Inserted';
        root.insertBefore(inserted, root.firstChild);
        checks.insertion = matchesOracle(root);
        cached(root);
        inserted.remove();
        checks.deletion = matchesOracle(root);

        // A block can mutate while detached. Reattachment to a different
        // parent and reattachment to the same parent after a microtask both
        // need fresh serialization.
        const detached = root.querySelector('p');
        cached(root);
        detached.remove();
        detached.textContent = 'Changed while detached';
        document.body.append(detached);
        checks.attachDifferentRoot = matchesOracle(root);
        detached.remove();
        await Promise.resolve();
        root.append(detached);
        checks.reattachSameRoot = matchesOracle(root);

        // Paste through the live visual editor paste handler, then compare
        // against a direct serialization of the resulting DOM.
        const paste = new DataTransfer();
        paste.setData('text/html', '<p>Pasted <strong>content</strong></p>');
        root.focus();
        const range = document.createRange();
        range.selectNodeContents(root);
        range.collapse(false);
        getSelection().removeAllRanges();
        getSelection().addRange(range);
        root.dispatchEvent(new ClipboardEvent('paste', { clipboardData: paste, bubbles: true, cancelable: true }));
        checks.paste = matchesOracle(root);

        // Once unbound the serializer must still be correct; when asked again
        // while connected it may safely install a fresh observer/cache.
        cached(root);
        sync.unbindVisualSurface(root);
        root.querySelector('li').setAttribute('data-checked', 'false');
        checks.afterUnbind = matchesOracle(root);
        checks.rebound = matchesOracle(root);

        // Detached roots bypass cache construction and are still serialized
        // directly. Reconnect after a detached mutation to exercise rebind.
        const other = document.createElement('div');
        other.innerHTML = '<p>Before detach</p>';
        document.body.append(other);
        cached(other);
        other.remove();
        other.firstElementChild.textContent = 'Mutated detached';
        checks.detachedNoObserver = matchesOracle(other);
        document.body.append(other);
        checks.reconnected = matchesOracle(other);
        sync.unbindVisualSurface(other);

        // Explicitly disable the platform API while the root is connected to
        // verify the uncached fallback path, rather than only detached behavior.
        const NativeMutationObserver = window.MutationObserver;
        window.MutationObserver = undefined;
        try {
          root.querySelector('p').textContent = 'No observer fallback';
          checks.noObserverFallback = matchesOracle(root);
        } finally {
          window.MutationObserver = NativeMutationObserver;
        }

        return checks;
      });
      await finish({ result });
    """)

    assert all(data["result"].values()), data["result"]
