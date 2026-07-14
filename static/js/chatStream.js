// static/js/chatStream.js
// SSE event handlers extracted from chat.js handleChatSubmit
// Handles: ui_control events, background stream management

import uiModule from './ui.js';
import Storage from './storage.js';
import themeModule from './theme.js';
import markdownModule from './markdown.js';
import sessionModule from './sessions.js';

/**
 * Handle a ui_control SSE event — AI-driven UI manipulation.
 * Extracted from the duplicated ui_control + tool_output.ui_event handlers.
 */
export function handleUIControl(uiData) {
  var uiEvent = uiData.ui_event || uiData;
  var esc = uiModule.esc;

  try {
    if (uiEvent === 'toggle' || uiData.ui_event === 'toggle') {
      var toggleMap = {
        web: 'web-toggle', bash: 'bash-toggle', rag: 'rag-toggle',
        research: 'research-toggle', incognito: 'incognito-toggle',
      };
      var btnMap = {
        web: 'web-toggle-btn', bash: 'bash-toggle-btn', rag: 'rag-indicator-btn',
      };
      var chkId = toggleMap[uiData.toggle_name];
      var btnId = btnMap[uiData.toggle_name];
      if (uiData.toggle_name === 'rag' && window._syncRagIndicator) {
        window._syncRagIndicator(!!uiData.state);
      } else {
        if (chkId) {
          var chk = document.getElementById(chkId);
          if (chk) chk.checked = !!uiData.state;
        }
        if (btnId) {
          var btn = document.getElementById(btnId);
          if (btn) btn.classList.toggle('active', !!uiData.state);
        }
      }
      var ts = Storage.getJSON(Storage.KEYS.TOGGLES, {});
      ts[uiData.toggle_name] = !!uiData.state;
      Storage.setJSON(Storage.KEYS.TOGGLES, ts);

    } else if (uiEvent === 'set_mode' || uiData.ui_event === 'set_mode') {
      var modeVal = uiData.mode;
      var agentBtn = document.getElementById('mode-agent-btn');
      var chatBtn = document.getElementById('mode-chat-btn');
      if (agentBtn && chatBtn) {
        agentBtn.classList.toggle('active', modeVal === 'agent');
        chatBtn.classList.toggle('active', modeVal !== 'agent');
      }
      var ts2 = Storage.getJSON(Storage.KEYS.TOGGLES, {});
      ts2.mode = modeVal;
      Storage.setJSON(Storage.KEYS.TOGGLES, ts2);
      document.querySelectorAll('[data-mode-tool]').forEach(function(b) {
        b.style.display = modeVal === 'agent' ? '' : 'none';
      });

    } else if (uiEvent === 'switch_model' || uiData.ui_event === 'switch_model') {
      var modelDisplay = document.querySelector('.current-model-name, #current-model');
      if (modelDisplay) modelDisplay.textContent = uiData.model;

    } else if (uiEvent === 'set_theme' || uiData.ui_event === 'set_theme') {
      var tm = themeModule;
      if (tm && tm.THEMES && tm.applyColors && tm.save) {
        var themeName = uiData.theme_name;
        if (themeName === 'chatgpt') themeName = 'gpt';  // renamed preset
        var customThemes = tm.getCustomThemes ? tm.getCustomThemes() : {};
        var colors = tm.THEMES[themeName] || customThemes[themeName] || uiData.colors;
        if (colors) {
          tm.applyColors(colors);
          tm.save(themeName, colors);
          var grid = document.getElementById('themeGrid');
          if (grid) {
            grid.querySelectorAll('.theme-swatch').forEach(function(s) { s.classList.remove('active'); });
            var sw = grid.querySelector('[data-theme="' + themeName + '"]');
            if (sw) sw.classList.add('active');
          }
        }
      }

    } else if (uiEvent === 'create_theme' || uiData.ui_event === 'create_theme') {
      var tm2 = themeModule;
      if (tm2 && tm2.applyColors && tm2.save) {
        var colors2 = uiData.colors;
        var name = uiData.theme_name || 'custom';
        if (colors2) {
          tm2.applyColors(colors2);
          tm2.save(name, colors2);
          // Background effects (animated pattern / frosted glass) the model
          // optionally set — apply them live and persist with the theme so
          // they survive re-applying it later.
          var bg = uiData.bg || null;
          var opts = {};
          if (bg) {
            if (bg.pattern && tm2.applyBgPattern) { tm2.applyBgPattern(bg.pattern); opts.bgPattern = bg.pattern; }
            if (bg.effectColor && tm2.applyBgEffectColor) { tm2.applyBgEffectColor(bg.effectColor); opts.bgEffectColor = bg.effectColor; }
            if (bg.effectIntensity != null && tm2.applyBgEffectIntensity) { tm2.applyBgEffectIntensity(bg.effectIntensity); opts.bgEffectIntensity = bg.effectIntensity; }
            if (bg.effectSize != null && tm2.applyBgEffectSize) { tm2.applyBgEffectSize(bg.effectSize); opts.bgEffectSize = bg.effectSize; }
            if (bg.frosted != null && tm2.applyFrostedGlass) { tm2.applyFrostedGlass(bg.frosted); opts.frosted = bg.frosted; }
          }
          if (tm2.saveCustomTheme) tm2.saveCustomTheme(name, colors2, Object.keys(opts).length ? opts : undefined);
        }
      }

    } else if (uiEvent === 'highlight' || uiData.ui_event === 'highlight') {
      document.querySelectorAll('.odysseus-highlight').forEach(function(e) { e.classList.remove('odysseus-highlight'); });
      document.querySelectorAll('.odysseus-hl-label').forEach(function(e) { e.remove(); });
      var target = document.querySelector(uiData.selector);
      if (target) {
        target.classList.add('odysseus-highlight');
        target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        if (uiData.label) {
          var lbl = document.createElement('div');
          lbl.className = 'odysseus-hl-label';
          lbl.textContent = uiData.label;
          if (!target.style.position) target.style.position = 'relative';
          target.appendChild(lbl);
        }
      }

    } else if (uiEvent === 'clear_highlight' || uiData.ui_event === 'clear_highlight') {
      document.querySelectorAll('.odysseus-highlight').forEach(function(e) { e.classList.remove('odysseus-highlight'); });
      document.querySelectorAll('.odysseus-hl-label').forEach(function(e) { e.remove(); });

    } else if (uiEvent === 'research_started' || uiData.ui_event === 'research_started') {
      // Agent kicked off deep research — adopt the session into the
      // sidebar immediately so the user sees it without waiting for
      // the 12s active-poll.
      var rsid = uiData.research_session_id || uiData.session_id;
      if (rsid) {
        import('./research/jobs.js').then(function(mod) {
          var fn = mod.adoptSession || (mod.default && mod.default.adoptSession);
          if (fn) fn(rsid);
        }).catch(function(){});
        // The clickable "Open in Deep Research" link is now emitted by the
        // agent loop as a `#research-<id>` markdown anchor in the assistant's
        // response text — it renders as a regular clickable chat link AND
        // persists across refresh (saved with the message). No ephemeral
        // chip injection needed here anymore.
      }

    } else if (uiEvent === 'open_panel' || uiData.ui_event === 'open_panel') {
      var panel = uiData.panel;
      if (panel === 'documents') {
        import('./documentLibrary.js').then(function(mod) {
          var fn = mod.openLibrary || (mod.default && mod.default.openLibrary);
          if (fn) fn();
        }).catch(function(){});
      } else if (panel === 'gallery') {
        import('./gallery.js').then(function(mod) {
          var fn = mod.openGallery || (mod.default && mod.default.openGallery);
          if (fn) fn();
        }).catch(function(){});
      } else if (panel === 'email') {
        import('./emailLibrary.js').then(function(mod) {
          var fn = mod.openEmailLibrary || (mod.default && mod.default.openEmailLibrary);
          if (fn) fn();
        }).catch(function(){});
      } else if (panel === 'sessions') {
        import('./sessions.js').then(function(mod) {
          var fn = mod.openLibrary || (mod.default && mod.default.openLibrary);
          if (fn) fn();
        }).catch(function(){});
      } else if (panel === 'cookbook') {
        import('./cookbook.js').then(function(mod) {
          var fn = mod.open || (mod.default && mod.default.open);
          if (fn) fn();
        }).catch(function(){});
      } else if (panel === 'notes') {
        import('./notes.js').then(function(mod) {
          var fn = mod.openPanel || mod.openNotes || (mod.default && (mod.default.openPanel || mod.default.openNotes));
          if (fn) fn();
        }).catch(function(){});
      } else if (panel === 'memories' || panel === 'skills' || panel === 'settings') {
        // These live in the sidebar / settings drawer — most just need
        // an existing button click.
        var ids = { memories: 'tool-memory-btn', skills: 'skills-btn', settings: 'open-settings-btn' };
        var btn = document.getElementById(ids[panel]);
        if (btn) btn.click();
      }

    } else if (uiEvent === 'open_email_reply' || uiData.ui_event === 'open_email_reply') {
      import('./emailInbox.js').then(function(mod) {
        var fn = mod.openReplyDraft || (mod.default && mod.default.openReplyDraft);
        if (fn) fn(uiData.uid, uiData.folder || 'INBOX', uiData.mode || 'reply', uiData.body || '');
      }).catch(function(e) {
        console.warn('open_email_reply failed:', e);
      });
    }
  } catch(e) {
    console.warn('ui_control handler error:', e);
  }
}

/**
 * Notify user when a background stream completes.
 */
export function notifyStreamComplete(sessionId, query) {
  var isHidden = document.hidden;
  var isOtherSession = sessionModule && sessionModule.getCurrentSessionId() !== sessionId;
  if (!isHidden && !isOtherSession) return;
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  var body = query ? 'Response to "' + query.substring(0, 60) + '" is ready' : 'Your chat response has completed';
  var notification = new Notification('Response Complete', {
    body: body,
    tag: 'stream-' + sessionId,
  });
  notification.onclick = function() {
    window.focus();
    if (isOtherSession && sessionModule) {
      sessionModule.selectSession(sessionId);
    }
    notification.close();
  };
  setTimeout(function() { notification.close(); }, 10000);
}

/**
 * Insert a clickable in-chat toast when a background stream finishes.
 */
export function insertStreamDoneToast(sessionId, query) {
  var box = document.getElementById('chat-history');
  if (!box) return;
  var sessions = sessionModule ? sessionModule.getSessions() : [];
  var sess = sessions.find(function(s) { return s.id === sessionId; });
  var name = sess ? sess.name : 'another session';
  var preview = query ? '"' + query.substring(0, 50) + (query.length > 50 ? '...' : '') + '"' : '';
  var div = document.createElement('div');
  div.className = 'msg msg-system stream-done-toast';
  div.innerHTML = '<div class="body">'
    + '<span class="stream-done-indicator">●</span>'
    + '<span>Response ready in <strong>' + (name || 'session').replace(/</g, '&lt;') + '</strong>'
    + (preview ? ' &mdash; ' + preview.replace(/</g, '&lt;') : '')
    + '</span>'
    + '</div>';
  div.addEventListener('click', function() {
    if (sessionModule) sessionModule.selectSession(sessionId);
  });
  box.appendChild(div);
  uiModule.scrollHistory();
}

/**
 * Notify when research completes (browser notification).
 */
export function notifyResearchComplete(sessionId, query) {
  var isHidden = document.hidden;
  var isOtherSession = sessionModule && sessionModule.getCurrentSessionId() !== sessionId;
  if (!isHidden && !isOtherSession) return;
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  var body = query ? 'Research on "' + query.substring(0, 60) + '" is ready' : 'Your deep research has completed';
  var notification = new Notification('Research Complete', {
    body: body,
    tag: 'research-' + sessionId,
  });
  notification.onclick = function() {
    window.focus();
    if (isOtherSession && sessionModule) {
      sessionModule.selectSession(sessionId);
    }
    notification.close();
  };
  setTimeout(function() { notification.close(); }, 10000);
}

const chatStream = {
  handleUIControl,
  notifyStreamComplete,
  insertStreamDoneToast,
  notifyResearchComplete,
};

export default chatStream;
