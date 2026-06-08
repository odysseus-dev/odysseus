// ============================================
// i18n — Simple Internationalization Module
// ============================================

const translations = {
  en: {
    // Login Page
    'login.title': 'Login',
    'login.username': 'Username',
    'login.password': 'Password',
    'login.login_button': 'Sign In',
    'login.signup_prompt': "Don't have an account? ",
    'login.signin_prompt': 'Already have an account? ',
    'login.signup_button': 'Create Account',
    'login.invalid_credentials': 'Invalid username or password',
    'login.error': 'Login error. Please try again.',
    'login.remember_me': 'Remember me',
    'login.forgot_password': 'Forgot password?',
    
    // Settings
    'settings.language': 'Language',
  },
  es: {
    // Página de Inicio de Sesión
    'login.title': 'Iniciar Sesión',
    'login.username': 'Nombre de Usuario',
    'login.password': 'Contraseña',
    'login.login_button': 'Iniciar Sesión',
    'login.signup_prompt': "¿No tienes cuenta? ",
    'login.signin_prompt': '¿Ya tienes cuenta? ',
    'login.signup_button': 'Crear Cuenta',
    'login.invalid_credentials': 'Nombre de usuario o contraseña inválidos',
    'login.error': 'Error al iniciar sesión. Por favor, inténtelo de nuevo.',
    'login.remember_me': 'Recuérdame',
    'login.forgot_password': '¿Olvidó su contraseña?',
    
    // Configuración
    'settings.language': 'Idioma',
  }
};

class i18n {
  constructor() {
    // Load saved language from localStorage, default to 'en'
    this.currentLanguage = localStorage.getItem('odysseus-language') || 'en';
    // Fallback to 'en' if language not supported
    if (!translations[this.currentLanguage]) {
      this.currentLanguage = 'en';
    }
    this.listeners = [];
  }

  /**
   * Get translated string
   * @param {string} key - Translation key (e.g., 'chat.send')
   * @param {object} params - Optional parameters for dynamic strings
   * @returns {string} Translated string or key if not found
   */
  t(key, params = {}) {
    let text = translations[this.currentLanguage]?.[key] || 
               translations['en']?.[key] || 
               key;
    
    // Replace parameters if provided
    for (const [param, value] of Object.entries(params)) {
      text = text.replace(`{{${param}}}`, value);
    }
    
    return text;
  }

  /**
   * Set current language
   * @param {string} lang - Language code ('en', 'es', etc.)
   */
  setLanguage(lang) {
    if (translations[lang]) {
      this.currentLanguage = lang;
      localStorage.setItem('odysseus-language', lang);
      // Notify all listeners of language change
      this.listeners.forEach(callback => callback(lang));
    }
  }

  /**
   * Get current language
   * @returns {string} Current language code
   */
  getLanguage() {
    return this.currentLanguage;
  }

  /**
   * Get available languages
   * @returns {object} Object with language codes as keys
   */
  getAvailableLanguages() {
    return {
      en: 'English',
      es: 'Español'
    };
  }

  /**
   * Subscribe to language changes
   * @param {function} callback - Function to call when language changes
   */
  onLanguageChange(callback) {
    this.listeners.push(callback);
  }

  /**
   * Unsubscribe from language changes
   * @param {function} callback - Function to remove
   */
  offLanguageChange(callback) {
    this.listeners = this.listeners.filter(cb => cb !== callback);
  }
}

const i18nModule = new i18n();
export default i18nModule;
