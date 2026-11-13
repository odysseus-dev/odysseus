"""Unit and behavioral verification for the Inline Suggestions Engine.

Validates static/js/document/ui/suggestions.js and its integration into document.js:
1. Suggestion storage persistence round-trip.
2. Accept, dismiss, and clearAll action lifecycle.
3. Single and multiple suggestion card rendering, queue cycling, and batch deduplication.
4. Export contract and backward compatibility on documentModule.
"""

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_node_script(script: str) -> dict:
    """Execute a Node ESM script and parse JSON output."""
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert res.returncode == 0, f"Node script failed with code {res.returncode}:\n{res.stderr}\nScript:\n{script}"
    return json.loads(res.stdout)


def test_suggestion_storage_persistence_round_trip():
    """Verify suggestion persistence to and from storage."""
    script = r"""
      import {
        initSuggestions,
        _saveSuggestionsToStorage,
        _restoreSuggestionsFromStorage,
        getActiveSuggestions,
        setActiveSuggestions,
        getSuggestionTotal,
      } from './static/js/document/ui/suggestions.js';

      const memoryStore = new Map();
      const mockStorage = {
        getItem: (k) => memoryStore.get(k) || null,
        setItem: (k, v) => memoryStore.set(k, String(v)),
        removeItem: (k) => memoryStore.delete(k),
      };

      let activeDocId = 'doc-123';
      initSuggestions({
        getActiveDocId: () => activeDocId,
        storage: mockStorage,
      });

      const sampleSuggestions = [
        { id: 'sugg-1', find: 'quick brown', replace: 'swift red', reason: 'Style improvement' },
        { id: 'sugg-2', find: 'lazy dog', replace: 'sleepy hound', reason: 'Word choice' },
      ];

      // 1. Save suggestions for active doc
      setActiveSuggestions(sampleSuggestions);
      _saveSuggestionsToStorage();

      const rawStored = mockStorage.getItem('odysseus-suggestions-doc-123');
      const parsedStored = JSON.parse(rawStored);

      // 2. Clear in-memory suggestions
      setActiveSuggestions([]);
      const clearedCount = getActiveSuggestions().length;

      // 3. Restore suggestions from storage
      _restoreSuggestionsFromStorage('doc-123');
      const restored = getActiveSuggestions();
      const restoredTotal = getSuggestionTotal();

      // 4. Save empty queue removes entry from storage
      setActiveSuggestions([]);
      _saveSuggestionsToStorage('doc-123');
      const afterEmptyStore = mockStorage.getItem('odysseus-suggestions-doc-123');

      console.log(JSON.stringify({
        rawStoredExists: Boolean(rawStored),
        storedLength: parsedStored.length,
        firstItem: parsedStored[0],
        clearedCount,
        restoredLength: restored.length,
        restoredItems: restored.map(s => ({ id: s.id, find: s.find, replace: s.replace, reason: s.reason })),
        restoredTotal,
        afterEmptyStore,
      }));
    """
    data = _run_node_script(script)

    assert data["rawStoredExists"] is True
    assert data["storedLength"] == 2
    assert data["firstItem"] == {
        "id": "sugg-1",
        "find": "quick brown",
        "replace": "swift red",
        "reason": "Style improvement",
    }
    assert data["clearedCount"] == 0
    assert data["restoredLength"] == 2
    assert data["restoredTotal"] == 2
    assert data["restoredItems"] == [
        {"id": "sugg-1", "find": "quick brown", "replace": "swift red", "reason": "Style improvement"},
        {"id": "sugg-2", "find": "lazy dog", "replace": "sleepy hound", "reason": "Word choice"},
    ]
    assert data["afterEmptyStore"] is None


def test_suggestion_action_resolution_lifecycle():
    """Verify accept, dismiss, and clearAll resolution lifecycle."""
    script = r"""
      import {
        initSuggestions,
        acceptSuggestion,
        dismissSuggestion,
        clearAllSuggestions,
        getActiveSuggestions,
        setActiveSuggestions,
        setSuggestionTotal,
        _saveSuggestionsToStorage,
      } from './static/js/document/ui/suggestions.js';

      const memoryStore = new Map();
      const mockStorage = {
        getItem: (k) => memoryStore.get(k) || null,
        setItem: (k, v) => memoryStore.set(k, String(v)),
        removeItem: (k) => memoryStore.delete(k),
      };

      let docContent = 'The quick brown fox jumps over the lazy dog.';
      let savedCalls = [];
      const mockTextarea = {
        value: docContent,
        focus: () => {},
        setSelectionRange: () => {},
        scrollTop: 0,
        clientHeight: 400,
      };

      const docMap = new Map([
        ['doc-abc', { id: 'doc-abc', content: docContent, language: 'markdown' }]
      ]);

      initSuggestions({
        getTextarea: () => mockTextarea,
        getActiveDocId: () => 'doc-abc',
        getDocs: () => docMap,
        storage: mockStorage,
        saveDocument: (opts) => {
          savedCalls.push(opts);
          docContent = mockTextarea.value;
          docMap.get('doc-abc').content = docContent;
        },
        syncHighlighting: () => {},
        updateLineNumbers: () => {},
      });

      const initialSuggestions = [
        { id: 's1', find: 'quick brown', replace: 'swift red', reason: 'Clarity' },
        { id: 's2', find: 'jumps over', replace: 'leaps above', reason: 'Vigor' },
        { id: 's3', find: 'lazy dog', replace: 'sleepy hound', reason: 'Variety' },
      ];
      setActiveSuggestions(initialSuggestions);
      setSuggestionTotal(3);
      _saveSuggestionsToStorage('doc-abc');

      // 1. Accept suggestion s1
      acceptSuggestion('s1');
      const textAfterAccept = mockTextarea.value;
      const countAfterAccept = getActiveSuggestions().length;
      const remainingIdsAfterAccept = getActiveSuggestions().map(s => s.id);

      // 2. Dismiss suggestion s2
      dismissSuggestion('s2');
      const textAfterDismiss = mockTextarea.value;
      const countAfterDismiss = getActiveSuggestions().length;
      const remainingIdsAfterDismiss = getActiveSuggestions().map(s => s.id);

      // 3. Clear all suggestions (clearing remaining s3)
      clearAllSuggestions();
      const countAfterClearAll = getActiveSuggestions().length;
      const storedAfterClearAll = mockStorage.getItem('odysseus-suggestions-doc-abc');

      console.log(JSON.stringify({
        textAfterAccept,
        countAfterAccept,
        remainingIdsAfterAccept,
        savedCallsCount: savedCalls.length,
        textAfterDismiss,
        countAfterDismiss,
        remainingIdsAfterDismiss,
        countAfterClearAll,
        storedAfterClearAll,
      }));
    """
    data = _run_node_script(script)

    assert data["textAfterAccept"] == "The swift red fox jumps over the lazy dog."
    assert data["countAfterAccept"] == 2
    assert data["remainingIdsAfterAccept"] == ["s2", "s3"]
    assert data["savedCallsCount"] >= 1

    # Dismiss does not change textarea text
    assert data["textAfterDismiss"] == "The swift red fox jumps over the lazy dog."
    assert data["countAfterDismiss"] == 1
    assert data["remainingIdsAfterDismiss"] == ["s3"]

    # Clear all clears remaining suggestions and storage
    assert data["countAfterClearAll"] == 0
    assert data["storedAfterClearAll"] is None


def test_single_suggestion_card_rendering_and_interaction():
    """Verify single suggestion card DOM rendering and interaction in browser."""
    script = r"""
      import { chromium } from 'playwright';

      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);

      await page.setContent(`
        <div class="doc-editor-pane" style="position:relative; width:800px; height:600px;">
          <div id="doc-editor-wrap" style="position:relative; width:100%; height:100%;">
            <div id="doc-editor-highlight"><div id="doc-editor-code"></div></div>
            <textarea id="doc-editor-textarea" style="width:100%; height:100%; line-height:20px;">Initial draft content for testing suggestions.</textarea>
          </div>
          <div id="doc-suggestions-container" style="display:none;"></div>
        </div>
      `);

      const result = await page.evaluate(async () => {
        const mod = await import('/static/js/document/ui/suggestions.js');
        const ta = document.getElementById('doc-editor-textarea');
        const wrap = document.getElementById('doc-editor-wrap');
        const pane = document.querySelector('.doc-editor-pane');

        const docMap = new Map([
          ['doc-single', { id: 'doc-single', content: ta.value, language: 'markdown' }]
        ]);

        mod.initSuggestions({
          getTextarea: () => ta,
          getWrap: () => wrap,
          getPane: () => pane,
          getActiveDocId: () => 'doc-single',
          getDocs: () => docMap,
          isOpen: () => true,
          saveDocument: () => {},
          syncHighlighting: () => {},
        });

        // Feed single suggestion
        await mod.handleDocSuggestions({
          doc_id: 'doc-single',
          suggestions: [
            { id: 'sugg-one', find: 'Initial draft', replace: 'Polished', reason: 'Better tone & clarity' }
          ]
        });

        const card = document.getElementById('doc-suggestion-active');
        const counter = card ? card.querySelector('.doc-suggestion-counter')?.textContent.trim() : null;
        const reason = card ? card.querySelector('.doc-suggestion-reason')?.textContent.trim() : null;
        const acceptBtn = card ? card.querySelector('.doc-suggestion-accept') : null;
        const acceptAllBtn = card ? card.querySelector('.doc-suggestion-accept-all') : null;
        const skipBtn = card ? card.querySelector('.doc-suggestion-dismiss') : null;
        const closeBtn = card ? card.querySelector('.doc-suggestion-close') : null;

        const renderedInfo = {
          cardExists: Boolean(card),
          counter,
          reason,
          hasAcceptBtn: Boolean(acceptBtn),
          hasAcceptAllBtn: Boolean(acceptAllBtn),
          hasSkipBtn: Boolean(skipBtn),
          hasCloseBtn: Boolean(closeBtn),
          cardClass: card?.className,
        };

        // Click Accept button
        acceptBtn.click();
        const appliedLength = mod.getActiveSuggestions().length;
        const textAfterClick = ta.value;

        return { renderedInfo, appliedLength, textAfterClick };
      });

      console.log(JSON.stringify(result));
      await browser.close();
    """
    data = _run_node_script(script)
    r = data["renderedInfo"]

    assert r["cardExists"] is True
    assert r["counter"] == "1 / 1"
    assert r["reason"] == "Better tone & clarity"
    assert r["hasAcceptBtn"] is True
    assert r["hasAcceptAllBtn"] is True
    assert r["hasSkipBtn"] is True
    assert r["hasCloseBtn"] is True
    assert "doc-suggestion-card" in r["cardClass"]

    # Clicking Accept applied suggestion and cleared active suggestions
    assert data["appliedLength"] == 0
    assert data["textAfterClick"].startswith("Polished content")


def test_multiple_suggestions_card_navigation_and_batch_queueing():
    """Verify navigation, batch queueing, counter updates, and accept-all in browser."""
    script = r"""
      import { chromium } from 'playwright';

      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);

      await page.setContent(`
        <div class="doc-editor-pane" style="position:relative; width:800px; height:600px;">
          <div id="doc-editor-wrap" style="position:relative; width:100%; height:100%;">
            <div id="doc-editor-highlight"><div id="doc-editor-code"></div></div>
            <textarea id="doc-editor-textarea" style="width:100%; height:100%; line-height:20px;">alpha bravo charlie delta</textarea>
          </div>
          <div id="doc-suggestions-container" style="display:none;"></div>
        </div>
      `);

      const result = await page.evaluate(async () => {
        const mod = await import('/static/js/document/ui/suggestions.js');
        const ta = document.getElementById('doc-editor-textarea');
        const wrap = document.getElementById('doc-editor-wrap');
        const pane = document.querySelector('.doc-editor-pane');

        const docMap = new Map([
          ['doc-multi', { id: 'doc-multi', content: ta.value, language: 'markdown' }]
        ]);

        mod.initSuggestions({
          getTextarea: () => ta,
          getWrap: () => wrap,
          getPane: () => pane,
          getActiveDocId: () => 'doc-multi',
          getDocs: () => docMap,
          isOpen: () => true,
          saveDocument: () => {},
          syncHighlighting: () => {},
        });

        // 1. Initial batch of 3 suggestions
        await mod.handleDocSuggestions({
          doc_id: 'doc-multi',
          suggestions: [
            { id: 's1', find: 'alpha', replace: 'ALPHA', reason: 'Capitalize alpha' },
            { id: 's2', find: 'bravo', replace: 'BRAVO', reason: 'Capitalize bravo' },
            { id: 's3', find: 'charlie', replace: 'CHARLIE', reason: 'Capitalize charlie' },
          ]
        });

        const getCard = () => document.getElementById('doc-suggestion-active');
        const counterInitial = getCard()?.querySelector('.doc-suggestion-counter')?.textContent.trim();
        const reasonInitial = getCard()?.querySelector('.doc-suggestion-reason')?.textContent.trim();

        // 2. Click Next to navigate to second suggestion
        const nextBtn = getCard().querySelector('.doc-suggestion-next');
        nextBtn.click();
        const counterAfterNext = getCard()?.querySelector('.doc-suggestion-counter')?.textContent.trim();
        const reasonAfterNext = getCard()?.querySelector('.doc-suggestion-reason')?.textContent.trim();

        // 3. Send follow-up batch while reviewing (includes duplicate ID 's1' and duplicate find 'alpha')
        await mod.handleDocSuggestions({
          doc_id: 'doc-multi',
          suggestions: [
            { id: 's1', find: 'alpha', replace: 'ALPHA_DUP', reason: 'Should be ignored' },
            { id: 's4', find: 'delta', replace: 'DELTA', reason: 'Capitalize delta' },
          ]
        });

        const totalAfterFollowUp = mod.getSuggestionTotal();
        const activeCountAfterFollowUp = mod.getActiveSuggestions().length;
        const counterAfterFollowUp = getCard()?.querySelector('.doc-suggestion-counter')?.textContent.trim();

        // 4. Click Accept All
        const acceptAllBtn = getCard().querySelector('.doc-suggestion-accept-all');
        acceptAllBtn.click();

        const finalText = ta.value;
        const finalActiveCount = mod.getActiveSuggestions().length;

        return {
          counterInitial,
          reasonInitial,
          counterAfterNext,
          reasonAfterNext,
          totalAfterFollowUp,
          activeCountAfterFollowUp,
          counterAfterFollowUp,
          finalText,
          finalActiveCount,
        };
      });

      console.log(JSON.stringify(result));
      await browser.close();
    """
    data = _run_node_script(script)

    assert data["counterInitial"] == "1 / 3"
    assert data["reasonInitial"] == "Capitalize alpha"

    # Navigation cycles to second suggestion
    assert data["reasonAfterNext"] == "Capitalize bravo"

    # Follow-up batch deduplicates s1 and adds s4 (total grows to 4)
    assert data["totalAfterFollowUp"] == 4
    assert data["activeCountAfterFollowUp"] == 4
    assert data["counterAfterFollowUp"] == "1 / 4"

    # Accept All replaces all targets and clears queue
    assert data["finalText"] == "ALPHA BRAVO CHARLIE DELTA"
    assert data["finalActiveCount"] == 0


def test_document_module_re_exports_suggestion_api():
    """Verify documentModule preserves handleDocSuggestions without leaking suggestion internals."""
    script = r"""
      import { chromium } from 'playwright';

      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);

      const result = await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=suggestions-verify-surface-1');
        const sugg = await import('/static/js/document/ui/suggestions.js');
        const def = mod.default;
        return {
          hasHandleDocSuggestionsNamed: typeof mod.handleDocSuggestions === 'function',
          hasAcceptSuggestionNamed: typeof mod.acceptSuggestion === 'function',
          hasDismissSuggestionNamed: typeof mod.dismissSuggestion === 'function',
          hasClearAllSuggestionsNamed: typeof mod.clearAllSuggestions === 'function',
          hasHandleDocSuggestionsDefault: typeof def.handleDocSuggestions === 'function',
          hasAcceptSuggestionDefault: typeof def.acceptSuggestion === 'function',
          hasDismissSuggestionDefault: typeof def.dismissSuggestion === 'function',
          hasClearAllSuggestionsDefault: typeof def.clearAllSuggestions === 'function',
          moduleExports: {
            initSuggestions: typeof sugg.initSuggestions === 'function',
            handleDocSuggestions: typeof sugg.handleDocSuggestions === 'function',
            acceptSuggestion: typeof sugg.acceptSuggestion === 'function',
            dismissSuggestion: typeof sugg.dismissSuggestion === 'function',
            clearAllSuggestions: typeof sugg.clearAllSuggestions === 'function',
          },
        };
      });

      console.log(JSON.stringify(result));
      await browser.close();
    """
    data = _run_node_script(script)

    assert data["hasHandleDocSuggestionsNamed"] is True
    assert data["hasAcceptSuggestionNamed"] is False
    assert data["hasDismissSuggestionNamed"] is False
    assert data["hasClearAllSuggestionsNamed"] is False
    assert data["hasHandleDocSuggestionsDefault"] is True
    assert data["hasAcceptSuggestionDefault"] is False
    assert data["hasDismissSuggestionDefault"] is False
    assert data["hasClearAllSuggestionsDefault"] is False
    assert all(data["moduleExports"].values())
