// ============================================
// Language Switcher Component
// ============================================

import i18nModule from './i18n.js';

const languageSwitcher = {
  /**
   * Create language switcher UI element
   * @returns {HTMLElement} Language switcher element
   */
  createSwitcher() {
    const container = document.createElement('div');
    container.style.cssText = 'display: flex; align-items: center; gap: 8px; justify-content: flex-end;';
    container.id = 'language-switcher';
    
    const label = document.createElement('label');
    label.textContent = i18nModule.t('settings.language');
    label.style.cssText = 'font-weight: 500; color: var(--fg); font-size: 14px;';
    
    const select = document.createElement('select');
    select.id = 'language-select';
    select.style.cssText = 'padding: 4px 8px; border: 1px solid var(--border); border-radius: 4px; background: color-mix(in srgb, var(--panel) 60%, transparent); color: var(--fg); font-size: 14px; cursor: pointer; font-family: inherit; outline: none;';
    
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
    
    select.addEventListener('change', (e) => {
      this.changeLanguage(e.target.value);
    });
    
    container.appendChild(label);
    container.appendChild(select);
    
    // Subscribe to language changes to update UI text
    i18nModule.onLanguageChange(() => {
      label.textContent = i18nModule.t('settings.language');
      this.updateSelectLabel(select);
    });
    
    return container;
  },

  /**
   * Update select option labels when language changes
   */
  updateSelectLabel(select) {
    const languages = i18nModule.getAvailableLanguages();
    Array.from(select.options).forEach((option, idx) => {
      const code = option.value;
      option.textContent = languages[code] || code;
    });
  },

  /**
   * Change language and update UI
   */
  changeLanguage(lang) {
    i18nModule.setLanguage(lang);
    this.updatePageLanguage();
  },

  /**
   * Update all translatable elements on the page
   */
  updatePageLanguage() {
    // Update all elements with data-i18n attribute
    document.querySelectorAll('[data-i18n]').forEach(el => {
      const key = el.getAttribute('data-i18n');
      el.textContent = i18nModule.t(key);
    });
    
    // Update all placeholders with data-i18n-placeholder attribute
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      const key = el.getAttribute('data-i18n-placeholder');
      el.placeholder = i18nModule.t(key);
    });
    
    // Update all titles with data-i18n-title attribute
    document.querySelectorAll('[data-i18n-title]').forEach(el => {
      const key = el.getAttribute('data-i18n-title');
      el.title = i18nModule.t(key);
    });
    
    // Update aria-label attributes
    document.querySelectorAll('[data-i18n-aria]').forEach(el => {
      const key = el.getAttribute('data-i18n-aria');
      el.setAttribute('aria-label', i18nModule.t(key));
    });
    
    // Update the language switcher
    const select = document.getElementById('language-select');
    if (select) {
      this.updateSelectLabel(select);
    }
    
    // Update HTML lang attribute
    document.documentElement.lang = i18nModule.getLanguage();
    
    // Dispatch custom event for modules to listen to
    window.dispatchEvent(new CustomEvent('odysseus:languageChanged', {
      detail: { language: i18nModule.getLanguage() }
    }));
  },

  /**
   * Initialize translations for a module
   * Update all data-i18n elements in a specific container
   */
  initializeContainer(container) {
    container.querySelectorAll('[data-i18n]').forEach(el => {
      const key = el.getAttribute('data-i18n');
      el.textContent = i18nModule.t(key);
    });
    
    container.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      const key = el.getAttribute('data-i18n-placeholder');
      el.placeholder = i18nModule.t(key);
    });
    
    container.querySelectorAll('[data-i18n-title]').forEach(el => {
      const key = el.getAttribute('data-i18n-title');
      el.title = i18nModule.t(key);
    });
  }
};

// Export both the module and the i18n instance
export default languageSwitcher;
export { i18nModule };
