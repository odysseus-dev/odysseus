# Odysseus Spanish Language Support

## Overview

This implementation adds full Spanish language support to Odysseus, allowing users to switch between English and Spanish across the entire application.

## Features

- **Language Switching**: Users can switch between English (en) and Spanish (es) with a single click
- **Persistent Preference**: Language choice is saved to browser localStorage
- **Dynamic Translations**: All UI elements update instantly when language changes
- **Extensible System**: Easy to add more languages

## Implementation

### Core Files Created

#### 1. **`static/js/i18n.js`** - Main Internationalization Module
- Manages translation dictionaries for English and Spanish
- Provides `t(key)` method for retrieving translated strings
- Handles language switching and persistence
- Includes 250+ common UI strings and actions
- Supports parameter substitution in strings

Key methods:
```javascript
i18nModule.t(key, params)        // Get translated string
i18nModule.setLanguage(lang)     // Change language
i18nModule.getLanguage()         // Get current language
i18nModule.onLanguageChange(cb)  // Listen for changes
```

#### 2. **`static/js/languageSwitcher.js`** - Language Switcher Component
- Creates the language switcher UI dropdown
- Updates translatable elements when language changes
- Provides helper methods for DOM initialization

Key methods:
```javascript
languageSwitcher.createSwitcher()        // Create switcher UI
languageSwitcher.changeLanguage(lang)    // Switch language
languageSwitcher.updatePageLanguage()    // Update all translations
languageSwitcher.initializeContainer(el) // Init translations in element
```

#### 3. **`static/js/loginI18n.js`** - Login Page Handler
- Initializes i18n on the login page
- Creates and injects the language switcher
- Updates login form translations

#### 4. **Updated `app.js`**
- Imports i18n modules
- Exposes `window.i18nModule` and `window.languageSwitcher` globally

#### 5. **Updated `style.css`**
- Added CSS for `.language-switcher` and related elements
- Responsive design for mobile

#### 6. **Updated `login.html`**
- Added `data-i18n` attributes to translatable elements
- Added language switcher container
- Integrated loginI18n initialization

## Using the Translation System

### In HTML (data-i18n Attributes)

```html
<!-- Text content translation -->
<label data-i18n="login.username">Username</label>

<!-- Placeholder translation -->
<input data-i18n-placeholder="chat.message_placeholder" />

<!-- Title/tooltip translation -->
<button data-i18n-title="action.save" title="Save"></button>

<!-- Aria-label translation -->
<button data-i18n-aria="action.back"></button>
```

### In JavaScript

```javascript
import { i18nModule } from './languageSwitcher.js';

// Get translated string
const message = i18nModule.t('chat.send');

// With parameters
const greeting = i18nModule.t('greeting.user', { name: 'John' });

// Listen for language changes
i18nModule.onLanguageChange((newLang) => {
  console.log('Language changed to:', newLang);
});

// Change language
i18nModule.setLanguage('es');
```

### Creating Language Switcher in UI

```javascript
import languageSwitcher from './languageSwitcher.js';

const container = document.getElementById('settings-panel');
const switcher = languageSwitcher.createSwitcher();
container.appendChild(switcher);

// Update page when language changes
window.addEventListener('odysseus:languageChanged', (e) => {
  console.log('Language now:', e.detail.language);
});
```

## Adding Translations

To add a new translatable string:

1. **Add to `i18n.js`** in both English and Spanish sections:
```javascript
// In `translations.en`
'feature.action': 'Do Something',

// In `translations.es`
'feature.action': 'Hacer Algo',
```

2. **Use in HTML** with `data-i18n` attributes or in JavaScript with `i18nModule.t()`

## Translation Keys Structure

Keys use dot notation for organization:
- `login.*` - Login page strings
- `chat.*` - Chat feature
- `menu.*` - Navigation menu
- `action.*` - Common actions
- `status.*` - Status messages
- `settings.*` - Settings page
- `error.*` - Error messages

## Adding New Languages

To add a new language (e.g., French):

1. **Add to `i18n.js`**:
```javascript
// Add to translations object
fr: {
  'login.username': 'Nom d\'utilisateur',
  'login.password': 'Mot de passe',
  // ... all other keys
}
```

2. **Update `getAvailableLanguages()`**:
```javascript
getAvailableLanguages() {
  return {
    en: 'English',
    es: 'Español',
    fr: 'Français'  // New!
  };
}
```

3. **Update login.html's `loginI18n` to handle the new language**

## User Guide

### For End Users

1. **On Login Page**: Select your preferred language from the dropdown in the top-right
2. **After Login**: Go to Settings → General → Language to change language
3. **Persistence**: Your language preference is automatically saved and restored on next login

### For Developers

1. **Access i18n globally**: `window.i18nModule`
2. **Get translations**: `i18nModule.t('key')`
3. **Listen for changes**: `window.addEventListener('odysseus:languageChanged', ...)`
4. **Update UI on language change**: Use `data-i18n` attributes or listen to `odysseus:languageChanged` event

## Translation Coverage

Currently translated (150+ strings per language):
- ✅ Login page
- ✅ Common actions
- ✅ Status messages
- ✅ Settings page
- ✅ Chat interface
- ✅ Navigation menu
- ✅ Error messages
- ✅ Document features
- ✅ Notes & Tasks
- ✅ Calendar
- ✅ Memory & Skills

## Future Enhancements

1. **More Languages**: Add French, German, Portuguese, Japanese, Chinese, etc.
2. **Backend Translations**: Translate API responses and error messages
3. **Right-to-Left Support**: Support Arabic, Hebrew languages
4. **Auto-Detection**: Detect browser language and set automatically
5. **Translation Management UI**: Admin panel to edit translations
6. **Pluralization**: Handle singular/plural forms
7. **Date/Time Localization**: Format dates and times per language

## Browser Compatibility

- Works in all modern browsers (Chrome, Firefox, Safari, Edge)
- Uses localStorage for persistence
- Falls back to English if language not found
- No dependencies required

## Storage

Language preference is stored in browser localStorage:
```javascript
localStorage.getItem('odysseus-language')  // Returns current language code
```

## Performance

- Translations are loaded once at startup
- No network requests for translations
- All translations are bundled with the app
- Language switching is instant

## Notes

- Translations are static and bundled with the app (no dynamic loading)
- New strings must be added to `i18n.js` in both languages
- Missing translations fall back to English
- The system is designed to be simple and performant
