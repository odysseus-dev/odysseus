// ============================================
// Login Page i18n Handler
// ============================================

import i18nModule from './i18n.js';
import languageSwitcher from './languageSwitcher.js';

const loginI18n = {
  /**
   * Initialize i18n for login page
   */
  init() {
    // Load the current language preference
    const currentLang = i18nModule.getLanguage();
    document.documentElement.lang = currentLang;
    
    // Translate all elements with data-i18n attribute
    this.translatePage();
    
    // Listen for language changes
    i18nModule.onLanguageChange(() => {
      this.translatePage();
    });
  },

  /**
   * Translate all elements on the page
   */
  translatePage() {
    // Update text content
    document.querySelectorAll('[data-i18n]').forEach(el => {
      const key = el.getAttribute('data-i18n');
      el.textContent = i18nModule.t(key);
    });
    
    // Update placeholders
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      const key = el.getAttribute('data-i18n-placeholder');
      el.placeholder = i18nModule.t(key);
    });
    
    // Update titles
    document.querySelectorAll('[data-i18n-title]').forEach(el => {
      const key = el.getAttribute('data-i18n-title');
      el.title = i18nModule.t(key);
    });
    
    // Update HTML lang attribute
    document.documentElement.lang = i18nModule.getLanguage();
  },

  /**
   * Create and inject language switcher into login page
   */
  createLanguageSwitcher() {
    const switcherContainer = document.getElementById('language-switcher-container');
    if (switcherContainer) {
      const switcher = languageSwitcher.createSwitcher();
      switcherContainer.appendChild(switcher);
    }
  }
};

export default loginI18n;
