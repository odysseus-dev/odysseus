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
    
    // Chat
    'chat.new_chat': 'New Chat',
    'chat.message_placeholder': 'Type a message...',
    'chat.send': 'Send',
    'chat.clear': 'Clear',
    'chat.delete': 'Delete',
    'chat.rename': 'Rename',
    'chat.save': 'Save',
    'chat.cancel': 'Cancel',
    'chat.copy': 'Copy',
    'chat.settings': 'Settings',
    'chat.model': 'Model',
    'chat.temperature': 'Temperature',
    'chat.max_tokens': 'Max Tokens',
    'chat.system_prompt': 'System Prompt',
    
    // Menu
    'menu.chat': 'Chat',
    'menu.agent': 'Agent',
    'menu.cookbook': 'Cookbook',
    'menu.research': 'Research',
    'menu.compare': 'Compare',
    'menu.documents': 'Documents',
    'menu.notes': 'Notes',
    'menu.tasks': 'Tasks',
    'menu.calendar': 'Calendar',
    'menu.email': 'Email',
    'menu.memory': 'Memory',
    'menu.skills': 'Skills',
    'menu.settings': 'Settings',
    'menu.help': 'Help',
    
    // Common Actions
    'action.save': 'Save',
    'action.cancel': 'Cancel',
    'action.delete': 'Delete',
    'action.edit': 'Edit',
    'action.add': 'Add',
    'action.create': 'Create',
    'action.update': 'Update',
    'action.close': 'Close',
    'action.back': 'Back',
    'action.next': 'Next',
    'action.yes': 'Yes',
    'action.no': 'No',
    'action.ok': 'OK',
    'action.copy': 'Copy',
    'action.paste': 'Paste',
    'action.cut': 'Cut',
    'action.search': 'Search',
    'action.filter': 'Filter',
    'action.sort': 'Sort',
    'action.export': 'Export',
    'action.import': 'Import',
    'action.download': 'Download',
    'action.upload': 'Upload',
    
    // Status Messages
    'status.loading': 'Loading...',
    'status.saving': 'Saving...',
    'status.saved': 'Saved',
    'status.error': 'Error',
    'status.success': 'Success',
    'status.warning': 'Warning',
    'status.info': 'Information',
    'status.connecting': 'Connecting...',
    'status.connected': 'Connected',
    'status.disconnected': 'Disconnected',
    
    // Settings
    'settings.title': 'Settings',
    'settings.general': 'General',
    'settings.appearance': 'Appearance',
    'settings.theme': 'Theme',
    'settings.language': 'Language',
    'settings.notifications': 'Notifications',
    'settings.privacy': 'Privacy',
    'settings.account': 'Account',
    'settings.password': 'Password',
    'settings.email': 'Email',
    'settings.api_keys': 'API Keys',
    'settings.two_factor': 'Two-Factor Authentication',
    'settings.logout': 'Logout',
    'settings.about': 'About',
    
    // Documents
    'document.new': 'New Document',
    'document.title': 'Document Title',
    'document.content': 'Content',
    'document.markdown': 'Markdown',
    'document.html': 'HTML',
    'document.csv': 'CSV',
    'document.print': 'Print',
    
    // Notes
    'notes.new': 'New Note',
    'notes.title': 'Note Title',
    'notes.add_tag': 'Add Tag',
    'notes.search_notes': 'Search Notes',
    'notes.archive': 'Archive',
    
    // Tasks
    'tasks.new': 'New Task',
    'tasks.title': 'Task Title',
    'tasks.due_date': 'Due Date',
    'tasks.priority': 'Priority',
    'tasks.status': 'Status',
    'tasks.complete': 'Complete',
    'tasks.incomplete': 'Incomplete',
    'tasks.high': 'High',
    'tasks.medium': 'Medium',
    'tasks.low': 'Low',
    
    // Calendar
    'calendar.today': 'Today',
    'calendar.this_week': 'This Week',
    'calendar.this_month': 'This Month',
    'calendar.new_event': 'New Event',
    'calendar.event_title': 'Event Title',
    'calendar.start_time': 'Start Time',
    'calendar.end_time': 'End Time',
    'calendar.all_day': 'All Day',
    
    // Memory & Skills
    'memory.memories': 'Memories',
    'memory.skills': 'Skills',
    'memory.add_memory': 'Add Memory',
    'memory.add_skill': 'Add Skill',
    'memory.search': 'Search Memory',
    
    // Models
    'models.available': 'Available Models',
    'models.download': 'Download',
    'models.serve': 'Serve',
    'models.stop': 'Stop',
    'models.vram': 'VRAM',
    'models.parameters': 'Parameters',
    
    // Error Messages
    'error.required_field': 'This field is required',
    'error.invalid_email': 'Invalid email address',
    'error.password_mismatch': 'Passwords do not match',
    'error.network': 'Network error',
    'error.server': 'Server error',
    'error.not_found': 'Not found',
    'error.unauthorized': 'Unauthorized',
    'error.forbidden': 'Forbidden',
    'error.timeout': 'Request timeout',
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
    
    // Chat
    'chat.new_chat': 'Nuevo Chat',
    'chat.message_placeholder': 'Escribe un mensaje...',
    'chat.send': 'Enviar',
    'chat.clear': 'Limpiar',
    'chat.delete': 'Eliminar',
    'chat.rename': 'Renombrar',
    'chat.save': 'Guardar',
    'chat.cancel': 'Cancelar',
    'chat.copy': 'Copiar',
    'chat.settings': 'Configuración',
    'chat.model': 'Modelo',
    'chat.temperature': 'Temperatura',
    'chat.max_tokens': 'Máx. Tokens',
    'chat.system_prompt': 'Indicación del Sistema',
    
    // Menú
    'menu.chat': 'Chat',
    'menu.agent': 'Agente',
    'menu.cookbook': 'Recetario',
    'menu.research': 'Investigación',
    'menu.compare': 'Comparar',
    'menu.documents': 'Documentos',
    'menu.notes': 'Notas',
    'menu.tasks': 'Tareas',
    'menu.calendar': 'Calendario',
    'menu.email': 'Correo',
    'menu.memory': 'Memoria',
    'menu.skills': 'Habilidades',
    'menu.settings': 'Configuración',
    'menu.help': 'Ayuda',
    
    // Acciones Comunes
    'action.save': 'Guardar',
    'action.cancel': 'Cancelar',
    'action.delete': 'Eliminar',
    'action.edit': 'Editar',
    'action.add': 'Añadir',
    'action.create': 'Crear',
    'action.update': 'Actualizar',
    'action.close': 'Cerrar',
    'action.back': 'Atrás',
    'action.next': 'Siguiente',
    'action.yes': 'Sí',
    'action.no': 'No',
    'action.ok': 'Aceptar',
    'action.copy': 'Copiar',
    'action.paste': 'Pegar',
    'action.cut': 'Cortar',
    'action.search': 'Buscar',
    'action.filter': 'Filtrar',
    'action.sort': 'Ordenar',
    'action.export': 'Exportar',
    'action.import': 'Importar',
    'action.download': 'Descargar',
    'action.upload': 'Subir',
    
    // Mensajes de Estado
    'status.loading': 'Cargando...',
    'status.saving': 'Guardando...',
    'status.saved': 'Guardado',
    'status.error': 'Error',
    'status.success': 'Éxito',
    'status.warning': 'Advertencia',
    'status.info': 'Información',
    'status.connecting': 'Conectando...',
    'status.connected': 'Conectado',
    'status.disconnected': 'Desconectado',
    
    // Configuración
    'settings.title': 'Configuración',
    'settings.general': 'General',
    'settings.appearance': 'Apariencia',
    'settings.theme': 'Tema',
    'settings.language': 'Idioma',
    'settings.notifications': 'Notificaciones',
    'settings.privacy': 'Privacidad',
    'settings.account': 'Cuenta',
    'settings.password': 'Contraseña',
    'settings.email': 'Correo',
    'settings.api_keys': 'Claves de API',
    'settings.two_factor': 'Autenticación de Dos Factores',
    'settings.logout': 'Cerrar Sesión',
    'settings.about': 'Acerca de',
    
    // Documentos
    'document.new': 'Nuevo Documento',
    'document.title': 'Título del Documento',
    'document.content': 'Contenido',
    'document.markdown': 'Markdown',
    'document.html': 'HTML',
    'document.csv': 'CSV',
    'document.print': 'Imprimir',
    
    // Notas
    'notes.new': 'Nueva Nota',
    'notes.title': 'Título de la Nota',
    'notes.add_tag': 'Añadir Etiqueta',
    'notes.search_notes': 'Buscar Notas',
    'notes.archive': 'Archivar',
    
    // Tareas
    'tasks.new': 'Nueva Tarea',
    'tasks.title': 'Título de la Tarea',
    'tasks.due_date': 'Fecha de Vencimiento',
    'tasks.priority': 'Prioridad',
    'tasks.status': 'Estado',
    'tasks.complete': 'Completada',
    'tasks.incomplete': 'Incompleta',
    'tasks.high': 'Alta',
    'tasks.medium': 'Media',
    'tasks.low': 'Baja',
    
    // Calendario
    'calendar.today': 'Hoy',
    'calendar.this_week': 'Esta Semana',
    'calendar.this_month': 'Este Mes',
    'calendar.new_event': 'Nuevo Evento',
    'calendar.event_title': 'Título del Evento',
    'calendar.start_time': 'Hora de Inicio',
    'calendar.end_time': 'Hora de Fin',
    'calendar.all_day': 'Todo el Día',
    
    // Memoria y Habilidades
    'memory.memories': 'Memorias',
    'memory.skills': 'Habilidades',
    'memory.add_memory': 'Añadir Memoria',
    'memory.add_skill': 'Añadir Habilidad',
    'memory.search': 'Buscar Memoria',
    
    // Modelos
    'models.available': 'Modelos Disponibles',
    'models.download': 'Descargar',
    'models.serve': 'Servir',
    'models.stop': 'Detener',
    'models.vram': 'VRAM',
    'models.parameters': 'Parámetros',
    
    // Mensajes de Error
    'error.required_field': 'Este campo es obligatorio',
    'error.invalid_email': 'Dirección de correo inválida',
    'error.password_mismatch': 'Las contraseñas no coinciden',
    'error.network': 'Error de red',
    'error.server': 'Error del servidor',
    'error.not_found': 'No encontrado',
    'error.unauthorized': 'No autorizado',
    'error.forbidden': 'Prohibido',
    'error.timeout': 'Tiempo de espera agotado',
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
