/**
 * telegram.js - Telegram integration UI component
 * 
 * Manages Telegram settings, account linking, and configuration
 * in the Odysseus integrations panel.
 */

const TelegramIntegration = (() => {
  const API_BASE = '/api/telegram';
  let containerId = 'telegram-integration-panel';
  
  let currentConfig = null;
  let linkingInProgress = false;
  let configSaveInProgress = false;
  let pendingRefreshTimer = null;

  /**
   * Initialize the Telegram integration UI
   */
  async function init() {
    try {
      clearPendingRefresh();
      await loadConfig();
      renderUI();
    } catch (error) {
      console.error('Failed to initialize Telegram integration:', error);
    }
  }

  /**
   * Load current Telegram configuration from server
   */
  async function loadConfig() {
    try {
      const response = await fetch(`${API_BASE}/config`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      currentConfig = await response.json();
    } catch (error) {
      console.error('Failed to load Telegram config:', error);
      currentConfig = {
        enabled: false,
        bot_token_configured: false,
        user_linked: false,
        chat_id: null,
        last_update: null,
        chat_mode: 'chat',
        forum_chat_id: null,
        forum_chat_title: '',
        topic_count: 0,
      };
    }
  }

  /**
   * Render the Telegram integration panel
   */
  function renderUI() {
    const container = document.getElementById(containerId);
    if (!container) {
      clearPendingRefresh();
      return;
    }

    const botLabel = currentConfig.bot_name || currentConfig.bot_username
      ? `${escapeHtml(currentConfig.bot_name || 'Telegram Bot')}${currentConfig.bot_username ? ` (@${escapeHtml(currentConfig.bot_username)})` : ''}`
      : 'Not configured';

    const html = `
      <div class="telegram-info-box" id="telegram-message-box" style="display:none"></div>

      ${currentConfig.can_manage_bot ? `
        <div>
          <div class="telegram-section-title">Bot Configuration</div>
          <div class="telegram-status-badge ${currentConfig.bot_token_configured ? 'linked' : 'unlinked'}">
            ${currentConfig.bot_token_configured ? 'Configured' : 'Not configured'}
          </div>
          <div class="telegram-info-box" style="margin-top:10px;">
            <strong>Bot:</strong> ${botLabel}<br>
            <strong>Source:</strong> ${escapeHtml(currentConfig.config_source || 'none')}
            ${currentConfig.managed_by_env ? '<br><strong>Note:</strong> This token is managed by the TELEGRAM_BOT_TOKEN environment variable and cannot be edited here.' : ''}
          </div>
          ${!currentConfig.managed_by_env ? `
            <div class="telegram-token-input-group" style="margin-top:12px;">
              <label for="telegram-bot-token-input">Telegram bot token</label>
              <input id="telegram-bot-token-input" type="password" placeholder="${currentConfig.bot_token_configured ? 'Paste a new token to replace the current one' : '123456789:AA...'}" autocomplete="off">
            </div>
            <div class="telegram-button-group">
              <button id="telegram-save-config-btn" class="telegram-btn primary" ${configSaveInProgress ? 'disabled' : ''}>Save Bot Token</button>
              ${currentConfig.bot_token_configured ? '<button id="telegram-clear-config-btn" class="telegram-btn">Clear Saved Token</button>' : ''}
            </div>
          ` : ''}
        </div>
      ` : ''}

      <div>
        <div class="telegram-section-title">Account Linking</div>
        <div class="telegram-status-badge ${currentConfig.user_linked ? 'linked' : 'unlinked'}">
          ${currentConfig.user_linked ? 'Linked' : 'Not linked'}
        </div>

        ${currentConfig.user_linked && currentConfig.chat_id ? `
          <div class="telegram-chat-id-display" style="margin-top:10px;">
            <strong>Chat ID:</strong> <span>${maskChatId(currentConfig.chat_id)}</span>
          </div>
        ` : ''}

        <div class="telegram-mode-section" style="margin-top:12px;">
          <div class="telegram-section-title" style="margin-bottom:8px;">Conversation Mode</div>
          <div class="telegram-info-box" style="margin-top:0;">
            <strong>Current mode:</strong> ${escapeHtml(currentConfig.chat_mode || 'chat')}<br>
            Chat mode gives direct replies. Agent mode can use Odysseus tools like web search and deep research when needed.
          </div>
          <div class="telegram-button-group">
            <button id="telegram-mode-chat-btn" class="telegram-btn ${currentConfig.chat_mode === 'chat' ? 'primary' : ''}">Chat</button>
            <button id="telegram-mode-agent-btn" class="telegram-btn ${currentConfig.chat_mode === 'agent' ? 'primary' : ''}">Agent</button>
          </div>
        </div>

        <div class="telegram-mode-section" style="margin-top:12px;">
          <div class="telegram-section-title" style="margin-bottom:8px;">Forum Topics</div>
          <div class="telegram-info-box" style="margin-top:0;">
            ${currentConfig.forum_chat_id
              ? `<strong>Detected forum chat:</strong> ${escapeHtml(currentConfig.forum_chat_title || 'Telegram group')} (${maskChatId(currentConfig.forum_chat_id)})<br><strong>Synced topics:</strong> ${Number(currentConfig.topic_count || 0)}`
              : 'Add the bot to a Telegram forum-enabled group chat and send a message there once. Odysseus will detect that chat and can then create a topic for each existing chat session.'}
          </div>
          <div class="telegram-button-group">
            <button id="telegram-sync-topics-btn" class="telegram-btn ${currentConfig.forum_chat_id ? 'primary' : ''}" ${currentConfig.user_linked && currentConfig.forum_chat_id ? '' : 'disabled'}>Sync Topics</button>
          </div>
        </div>

        ${currentConfig.bot_token_configured ? `
          ${!currentConfig.user_linked ? `
            <div class="telegram-info-box" style="margin-top:10px;">
              1. Open Telegram and start a chat with ${currentConfig.bot_username ? `<strong>@${escapeHtml(currentConfig.bot_username)}</strong>` : 'your bot'}<br>
              2. Send <code>/start</code> to receive a linking token<br>
              3. Paste that token below and click <strong>Link Account</strong>
            </div>
            <div class="telegram-token-input-group" style="margin-top:12px;">
              <label for="telegram-link-token-input">Linking token</label>
              <input id="telegram-link-token-input" type="text" placeholder="Paste the token from Telegram" autocomplete="off">
            </div>
            <div class="telegram-button-group">
              <button id="telegram-start-link-btn" class="telegram-btn primary" ${linkingInProgress ? 'disabled' : ''}>Link Account</button>
            </div>
          ` : `
            <div class="telegram-info-box" style="margin-top:10px;">
              Your Telegram account is linked. Send messages to the bot to chat with Odysseus.
            </div>
            <div class="telegram-button-group">
              <button id="telegram-unlink-btn" class="telegram-btn">Unlink Account</button>
            </div>
          `}
        ` : `
          <div class="telegram-info-box" style="margin-top:10px;">
            ${currentConfig.can_manage_bot
              ? 'Configure a Telegram bot token above, then users can link their Telegram accounts here.'
              : 'Ask an administrator to configure the Telegram bot token first. Once that is done, you can link your account here.'}
          </div>
        `}
      </div>
    `;

    container.innerHTML = html;
    attachEventListeners();
    scheduleDetectionRefresh();
  }

  function mount(targetContainerId = 'telegram-integration-panel') {
    containerId = targetContainerId || 'telegram-integration-panel';
    return init();
  }

  function clearPendingRefresh() {
    if (pendingRefreshTimer) {
      clearTimeout(pendingRefreshTimer);
      pendingRefreshTimer = null;
    }
  }

  function scheduleDetectionRefresh() {
    clearPendingRefresh();
    if (!currentConfig || !currentConfig.bot_token_configured || !currentConfig.user_linked || currentConfig.forum_chat_id) {
      return;
    }
    pendingRefreshTimer = setTimeout(async () => {
      try {
        const container = document.getElementById(containerId);
        if (!container) {
          clearPendingRefresh();
          return;
        }
        await loadConfig();
        renderUI();
      } catch (error) {
        console.error('Refreshing Telegram forum status failed:', error);
      }
    }, 5000);
  }

  /**
   * Attach event listeners to buttons
   */
  function attachEventListeners() {
    const saveConfigBtn = document.getElementById('telegram-save-config-btn');
    if (saveConfigBtn) {
      saveConfigBtn.addEventListener('click', handleSaveConfig);
    }

    const clearConfigBtn = document.getElementById('telegram-clear-config-btn');
    if (clearConfigBtn) {
      clearConfigBtn.addEventListener('click', handleClearConfig);
    }

    const startBtn = document.getElementById('telegram-start-link-btn');
    if (startBtn) {
      startBtn.addEventListener('click', handleStartLinking);
    }

    const unlinkBtn = document.getElementById('telegram-unlink-btn');
    if (unlinkBtn) {
      unlinkBtn.addEventListener('click', handleUnlink);
    }

    const chatModeBtn = document.getElementById('telegram-mode-chat-btn');
    if (chatModeBtn) {
      chatModeBtn.addEventListener('click', () => handleUpdateMode('chat'));
    }

    const agentModeBtn = document.getElementById('telegram-mode-agent-btn');
    if (agentModeBtn) {
      agentModeBtn.addEventListener('click', () => handleUpdateMode('agent'));
    }

    const syncTopicsBtn = document.getElementById('telegram-sync-topics-btn');
    if (syncTopicsBtn) {
      syncTopicsBtn.addEventListener('click', handleSyncTopics);
    }
  }

  /**
   * Save bot configuration.
   */
  async function handleSaveConfig() {
    const input = document.getElementById('telegram-bot-token-input');
    const token = (input && input.value ? input.value : '').trim();
    if (!token) {
      showMessage('Enter a Telegram bot token first.', 'error');
      return;
    }

    try {
      configSaveInProgress = true;
      const response = await fetch(`${API_BASE}/config`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bot_token: token }),
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || 'Failed to save Telegram bot token');
      }

      showMessage(data.message || 'Telegram bot configured successfully.', 'success');
      await loadConfig();
      renderUI();
      try { window.dispatchEvent(new CustomEvent('odysseus-integrations-changed')); } catch (_) {}
    } catch (error) {
      console.error('Saving Telegram config failed:', error);
      showMessage(error.message, 'error');
    } finally {
      configSaveInProgress = false;
    }
  }

  /**
   * Clear saved bot configuration.
   */
  async function handleClearConfig() {
    if (!confirm('Clear the saved Telegram bot token? Users will no longer be able to chat until a new token is configured.')) {
      return;
    }

    async function handleSyncTopics() {
      try {
        const response = await fetch(`${API_BASE}/sync-topics`, {
          method: 'POST',
          credentials: 'same-origin',
        });

        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data.detail || 'Failed to sync Telegram topics');
        }

        const created = Number(data.created_count || 0);
        const updated = Number(data.updated_count || 0);
        const skipped = Number(data.skipped_count || 0);
        showMessage(`Telegram topics synced: ${created} created, ${updated} updated, ${skipped} unchanged.`, 'success');
        await loadConfig();
        renderUI();
        try { window.dispatchEvent(new CustomEvent('odysseus-integrations-changed')); } catch (_) {}
      } catch (error) {
        console.error('Syncing Telegram topics failed:', error);
        showMessage(error.message, 'error');
      }
    }

    try {
      const response = await fetch(`${API_BASE}/config`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bot_token: '' }),
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || 'Failed to clear Telegram bot token');
      }

      showMessage(data.message || 'Telegram bot configuration cleared.', 'success');
      await loadConfig();
      renderUI();
      try { window.dispatchEvent(new CustomEvent('odysseus-integrations-changed')); } catch (_) {}
    } catch (error) {
      console.error('Clearing Telegram config failed:', error);
      showMessage(error.message, 'error');
    }
  }

  /**
   * Handle start linking button click
   */
  async function handleStartLinking() {
    const input = document.getElementById('telegram-link-token-input');
    const token = (input && input.value ? input.value : '').trim();
    if (!token) return;

    try {
      linkingInProgress = true;
      const response = await fetch(`${API_BASE}/link`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ linking_token: token }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Failed to link account');
      }

      const result = await response.json();
      showMessage(result.message || 'Telegram account linked successfully.', 'success');
      
      // Reload config and re-render
      await loadConfig();
      renderUI();
      try { window.dispatchEvent(new CustomEvent('odysseus-integrations-changed')); } catch (_) {}
    } catch (error) {
      console.error('Linking failed:', error);
      showMessage(`Linking failed: ${error.message}`, 'error');
    } finally {
      linkingInProgress = false;
    }
  }

  /**
   * Handle unlink button click
   */
  async function handleUnlink() {
    if (!confirm('Unlink your Telegram account? You can re-link it later.')) {
      return;
    }

    try {
      const response = await fetch(`${API_BASE}/unlink`, {
        method: 'POST',
        credentials: 'same-origin',
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || `HTTP ${response.status}`);
      }

      showMessage(data.message || 'Telegram account unlinked.', 'success');
      
      // Reload config and re-render
      await loadConfig();
      renderUI();
      try { window.dispatchEvent(new CustomEvent('odysseus-integrations-changed')); } catch (_) {}
    } catch (error) {
      console.error('Unlinking failed:', error);
      showMessage(`Unlinking failed: ${error.message}`, 'error');
    }
  }

  async function handleUpdateMode(mode) {
    try {
      const response = await fetch(`${API_BASE}/mode`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || `HTTP ${response.status}`);
      }

      showMessage(data.message || `Telegram mode updated to ${mode}.`, 'success');
      await loadConfig();
      renderUI();
      try { window.dispatchEvent(new CustomEvent('odysseus-integrations-changed')); } catch (_) {}
    } catch (error) {
      console.error('Updating Telegram mode failed:', error);
      showMessage(`Failed to update mode: ${error.message}`, 'error');
    }
  }

  function showMessage(message, kind) {
    const box = document.getElementById('telegram-message-box');
    if (!box) {
      alert(message);
      return;
    }
    box.className = kind === 'error' ? 'telegram-error-message' : 'telegram-success-message';
    box.textContent = message;
    box.style.display = '';
  }

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  /**
   * Mask chat ID for display (show first/last 4 chars)
   */
  function maskChatId(chatId) {
    const str = String(chatId);
    if (str.length <= 8) return str;
    return `${str.slice(0, 4)}...${str.slice(-4)}`;
  }

  /**
   * Format timestamp for display
   */
  function formatDate(isoString) {
    if (!isoString) return 'Never';
    try {
      const date = new Date(isoString);
      return date.toLocaleString();
    } catch {
      return isoString;
    }
  }

  return {
    init,
    mount,
    loadConfig,
    renderUI,
  };
})();

window.TelegramIntegration = TelegramIntegration;

// Auto-initialize when document is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    TelegramIntegration.init();
  });
} else {
  TelegramIntegration.init();
}
