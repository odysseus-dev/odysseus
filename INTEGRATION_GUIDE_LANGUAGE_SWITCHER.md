/**
 * INTEGRATION GUIDE: Adding Language Switcher to Settings Panel
 * 
 * This file shows how to integrate the language switcher into the Settings panel
 * of Odysseus. Update the settings.js module to include language selection.
 */

// ============================================
// In static/js/settings.js
// ============================================

// 1. Add import at the top of settings.js:
import languageSwitcher, { i18nModule } from './languageSwitcher.js';

// 2. In the settings module initialization, add a language section:
const settingsModule = {
  async initializeSettings() {
    // ... existing code ...
    
    // Add General Settings Section
    this.createGeneralSection();
  },

  createGeneralSection() {
    const generalSection = document.querySelector('[data-section="general"]') || 
                          this.createSection('general', 'General');
    
    // Create language setting
    const languageControl = this.createLanguageControl();
    generalSection.appendChild(languageControl);
  },

  createLanguageControl() {
    const container = document.createElement('div');
    container.className = 'settings-group';
    container.id = 'language-settings';
    
    const label = document.createElement('label');
    label.textContent = i18nModule.t('settings.language');
    label.htmlFor = 'settings-language-select';
    label.className = 'settings-label';
    
    const select = document.createElement('select');
    select.id = 'settings-language-select';
    select.className = 'settings-select';
    
    // Get available languages
    const languages = i18nModule.getAvailableLanguages();
    for (const [code, name] of Object.entries(languages)) {
      const option = document.createElement('option');
      option.value = code;
      option.textContent = name;
      if (code === i18nModule.getLanguage()) {
        option.selected = true;
      }
      select.appendChild(option);
    }
    
    // Handle language change
    select.addEventListener('change', (e) => {
      const newLang = e.target.value;
      languageSwitcher.changeLanguage(newLang);
      this.saveSettings('language', newLang);
      
      // Show confirmation
      this.showNotification(`Language changed to ${languages[newLang]}`);
    });
    
    // Listen to language changes from other sources
    i18nModule.onLanguageChange((newLang) => {
      select.value = newLang;
      label.textContent = i18nModule.t('settings.language');
    });
    
    container.appendChild(label);
    container.appendChild(select);
    
    return container;
  },

  saveSettings(key, value) {
    // Save to backend or localStorage
    localStorage.setItem(`odysseus-setting-${key}`, JSON.stringify(value));
  }
};

// ============================================
// CSS Additions for settings.css
// ============================================

// Add to your settings stylesheet:

.settings-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 16px 0;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel);
}

.settings-label {
  font-weight: 500;
  color: var(--fg);
  font-size: 14px;
}

.settings-select {
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
  color: var(--fg);
  font-size: 14px;
  cursor: pointer;
  transition: border-color 0.12s ease, background 0.12s ease;
  font-family: inherit;
}

.settings-select:hover {
  border-color: var(--accent, var(--red));
}

.settings-select:focus {
  outline: none;
  border-color: var(--accent, var(--red));
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent, var(--red)) 20%, transparent);
}

// ============================================
// ADVANCED: Custom Language Detection
// ============================================

// Detect user's browser language and auto-set:

function detectAndSetLanguage() {
  const supported = Object.keys(i18nModule.getAvailableLanguages());
  
  // Check localStorage first
  const saved = localStorage.getItem('odysseus-language');
  if (saved && supported.includes(saved)) {
    return i18nModule.setLanguage(saved);
  }
  
  // Then check browser language
  const browserLang = navigator.language || navigator.userLanguage;
  const langCode = browserLang.split('-')[0]; // Get 'es' from 'es-ES'
  
  if (supported.includes(langCode)) {
    i18nModule.setLanguage(langCode);
  }
}

// Call this on app init:
detectAndSetLanguage();

// ============================================
// ADVANCED: Per-User Language Preference
// ============================================

// Store language preference with user profile:

async function saveuserLanguagePreference(userId, language) {
  await fetch('/api/users/' + userId + '/preferences', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ language })
  });
}

async function loadUserLanguagePreference(userId) {
  const res = await fetch('/api/users/' + userId + '/preferences');
  if (res.ok) {
    const prefs = await res.json();
    if (prefs.language) {
      i18nModule.setLanguage(prefs.language);
    }
  }
}

// ============================================
// EXAMPLE: Listening to Language Changes
// ============================================

// Update page title based on language:
i18nModule.onLanguageChange((lang) => {
  const title = i18nModule.t('login.title');
  document.title = 'Odysseus - ' + title;
});

// Refresh specific UI elements:
window.addEventListener('odysseus:languageChanged', (e) => {
  console.log('Reloading UI for language:', e.detail.language);
  
  // Refresh any dynamic content that wasn't caught by data-i18n
  updateDynamicUIText();
});

// ============================================
// Testing the Translation System
// ============================================

// In browser console:
// Get current language
window.i18nModule.getLanguage();

// Switch language
window.i18nModule.setLanguage('es');
window.i18nModule.setLanguage('en');

// Get translation
window.i18nModule.t('chat.send');

// Check available languages
window.i18nModule.getAvailableLanguages();

// Listen to changes
window.i18nModule.onLanguageChange(lang => console.log('Changed to:', lang));
