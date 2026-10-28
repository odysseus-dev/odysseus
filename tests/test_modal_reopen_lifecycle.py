"""Real ESM/DOM regressions for Settings and Theme close/restore races."""
import json
import subprocess

import pytest

from tests.helpers.stylesheets import stylesheet_link_tags


@pytest.mark.parametrize('modal_id', ['settings-modal', 'theme-modal'])
@pytest.mark.parametrize('width', [1440, 390])
def test_modal_reopen_after_dock_close_and_pending_animation(modal_id, width):
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({headless:true});
      try {
        const page = await browser.newPage({viewport:{width:WIDTH,height:1000}});
        await page.route('**/api/**', route => route.fulfill({json:{value:null}}));
        await page.goto(process.env.ODYSSEUS_TEST_STATIC_ORIGIN+'/static/js/storage.js');
        await page.setContent('__STYLES__<div id="sidebar"></div>');
        const result = await page.evaluate(async id => {
          const html = await (await fetch('/static/index.html')).text();
          const source = new DOMParser().parseFromString(html,'text/html');
          document.body.append(source.getElementById(id));
          const modal = document.getElementById(id);
          const content = modal.querySelector('.modal-content');
          const manager = await import('/static/js/modalManager.js');
          let open, close;
          if(id === 'settings-modal') {
            const lifecycle = await import('/static/js/settings/lifecycle.js');
            open = () => lifecycle.showSettingsModal(modal);
            close = () => lifecycle.hideSettingsModal(modal);
          } else {
            const theme = await import('/static/js/theme.js');
            // The old sidebar simply removed hidden; the public opener now
            // also cancels a pending close and restores manager state.
            open = theme.openPopup || (() => modal.classList.remove('hidden'));
            close = () => theme.closePopup();
          }
          const visible = () => getComputedStyle(modal).display !== 'none';
          const wait = () => new Promise(resolve=>setTimeout(resolve,350));
          const states = {};
          open();
          manager.register(id,{closeFn:close,restoreFn:()=>{}});
          manager.minimize(id);
          manager.close(id);
          await wait();
          open();
          await wait();
          states.afterDockClose = visible();
          // An opening/descendant animation must not reach an orphan closer.
          content.dispatchEvent(new AnimationEvent('animationend',{bubbles:true,animationName:'modal-enter'}));
          states.afterOpeningAnimation = visible();
          // Begin a visible close, then reopen before its fallback expires.
          close();
          open();
          await wait();
          states.afterFastReopen = visible();
          manager.register(id,{closeFn:close,restoreFn:()=>{}});
          manager.minimize(id);
          open();
          states.directOpenRestoresMinimized = visible() && !manager.isMinimized(id);
          // A child animation must not terminate a normal parent close.
          close();
          content.querySelector('button').dispatchEvent(new AnimationEvent('animationend',{bubbles:true}));
          states.childAnimationDoesNotClose = !modal.classList.contains('hidden');
          content.dispatchEvent(new AnimationEvent('animationend',{bubbles:true,animationName:'modal-exit'}));
          states.normalCloseCompletes = modal.classList.contains('hidden') && !content.classList.contains('modal-closing');
          // Completing by animation must also remove the timer.
          open();
          await wait();
          states.reopenAfterCompletedAnimation = visible();
          close();
          await wait();
          states.fallbackCompletes = modal.classList.contains('hidden') && !content.classList.contains('modal-closing');
          return states;
        }, MODAL_ID);
        console.log(JSON.stringify(result));
      } finally { await browser.close(); }
    """
    script = script.replace('WIDTH', str(width)).replace('MODAL_ID', json.dumps(modal_id))
    script = script.replace('__STYLES__', stylesheet_link_tags())
    result = subprocess.run(['node','--input-type=module','-e',script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    states = json.loads(result.stdout.strip())
    assert all(states.values()), states


def test_modal_visibility_is_available_offline_before_first_open():
    script = r"""
      import assert from 'node:assert/strict';
      import { chromium } from 'playwright';
      const browser = await chromium.launch({headless:true});
      try {
        const context = await browser.newContext({serviceWorkers:'allow'});
        const page = await context.newPage();
        const origin = process.env.ODYSSEUS_TEST_STATIC_ORIGIN;
        await page.goto(origin+'/static/js/storage.js');
        await page.evaluate(async () => {
          await navigator.serviceWorker.register('/static/sw.js');
          await navigator.serviceWorker.ready;
          if(!navigator.serviceWorker.controller) await new Promise(resolve =>
            navigator.serviceWorker.addEventListener('controllerchange',resolve,{once:true}));
          const response = await caches.match('/static/js/modalVisibility.js');
          if(!response?.ok) throw new Error('Modal helper is missing from install-time precache');
        });
        await context.setOffline(true);
        const offline = await context.newPage();
        await offline.goto(origin+'/static/js/storage.js');
        const exports = await offline.evaluate(async () =>
          Object.keys(await import('/static/js/modalVisibility.js')).sort());
        assert.deepEqual(exports,['hideModal','showModal']);
      } finally { await browser.close(); }
    """
    result = subprocess.run(['node','--input-type=module','-e',script], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
