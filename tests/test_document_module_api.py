"""The document editor's public surface, pinned against the running module.

``static/js/document.js`` is becoming a re-export wrapper. Five modules import
its default export, ``static/index.html`` loads it, and ``documentLibrary.js``
is handed named functions through its config object -- so the surface is the
contract that decomposition must not change, and a method that quietly stops
being re-exported is a runtime ``TypeError`` in whichever panel used it.

This loads the module in a browser and reads what it actually exports, rather
than grepping the source for the literal object: after extraction the object
may be assembled from imports, and a source-shape assertion would pass while
the export was broken.
"""

import json
import os
import subprocess
from pathlib import Path

from tests.helpers.document_source import document_source

ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()

# Every key on the default export. Consumers reach the editor through this
# object, so removing one is a breaking change; adding one is not.
DEFAULT_EXPORT_KEYS = {
    "clearAll",
    "clearSelection",
    "closeLibrary",
    "closePanel",
    "createDocument",
    "ensureDocPanel",
    "ensureEmailDraftEnvelope",
    "ensurePaneMounted",
    "enterDiffMode",
    "exitDiffMode",
    "findEmailDocId",
    "focusEmailReplyBody",
    "getActiveEmailComposerContext",
    "getChatDocumentId",
    "getCurrentDocId",
    "getSelectionContext",
    "handleDocSuggestions",
    "handleDocUpdate",
    "init",
    "injectFreshDoc",
    "isDiffModeActive",
    "isLibraryOpen",
    "isPanelOpen",
    "loadDocument",
    "loadSessionDocs",
    "moveActiveDocumentToCurrentChat",
    "moveActiveDocumentToNewChat",
    "newDocument",
    "openEmailDraft",
    "openLibrary",
    "openPanel",
    "replaceEmailReplyBody",
    "restoreSelectionReference",
    "saveDocument",
    "streamDocDelta",
    "streamDocFinalize",
    "streamDocOpen",
    "swapSide",
}

# Named exports. `prepareDocumentOpen` is deliberately in this set and not on
# the default export: `documentLibrary.js` receives it through `initLibrary`'s
# config, and a browser test calls it off the module namespace.
NAMED_EXPORTS = {
    "clearAll",
    "closePanel",
    "createDocument",
    "ensureDocPanel",
    "ensureEmailDraftEnvelope",
    "findEmailDocId",
    "focusEmailReplyBody",
    "getActiveEmailComposerContext",
    "getChatDocumentId",
    "getCurrentDocId",
    "getSelectionContext",
    "handleDocSuggestions",
    "handleDocUpdate",
    "init",
    "injectFreshDoc",
    "isPanelOpen",
    "loadDocument",
    "loadSessionDocs",
    "newDocument",
    "openEmailDraft",
    "openPanel",
    "prepareDocumentOpen",
    "replaceEmailReplyBody",
    "restoreSelectionReference",
    "saveDocument",
    "streamDocDelta",
    "streamDocFinalize",
    "streamDocOpen",
    "swapSide",
}


def _module_surface():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      const surface = await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=module-api-surface-1');
        const fn = (o) => Object.keys(o).filter(k => typeof o[k] === 'function');
        return {
          named: Object.keys(mod).filter(k => k !== 'default'),
          namedFunctions: fn(mod).filter(k => k !== 'default'),
          defaultKeys: Object.keys(mod.default),
          defaultFunctions: fn(mod.default),
          globalIsSameObject: window.documentModule === mod.default,
        };
      });
      console.log(JSON.stringify(surface));
      await browser.close();
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_default_export_surface_is_complete_and_callable():
    surface = _module_surface()

    missing = DEFAULT_EXPORT_KEYS - set(surface["defaultKeys"])
    assert not missing, f"default export lost methods: {sorted(missing)}"

    not_callable = DEFAULT_EXPORT_KEYS - set(surface["defaultFunctions"])
    assert not not_callable, (
        f"default export keys that are not functions: {sorted(not_callable)}"
    )


def test_named_exports_survive_and_stay_callable():
    surface = _module_surface()

    missing = NAMED_EXPORTS - set(surface["named"])
    assert not missing, f"named exports lost: {sorted(missing)}"

    not_callable = NAMED_EXPORTS - set(surface["namedFunctions"])
    assert not not_callable, f"named exports that are not functions: {sorted(not_callable)}"


def test_window_bridge_is_the_default_export():
    """`window.documentModule` is a compatibility bridge no import graph shows.

    Consumers reach the editor off the global, so it must stay the same object
    as the default export rather than a second, partially wired copy.
    """
    assert "window.documentModule = documentModule" in DOC_JS
    assert _module_surface()["globalIsSameObject"] is True
