# ¡Bienvenido! / Welcome! 

## Quick Start: Spanish Language Support

Your Odysseus installation now includes **full Spanish language support**!

### 🚀 Getting Started

#### Option 1: Language Switcher on Login Page (Recommended)
1. Go to the login page: `http://localhost:7000/login`
2. Look for the **Language** dropdown in the top-right corner
3. Select **Español** from the dropdown
4. The entire login page will instantly translate to Spanish
5. Your choice is saved automatically

#### Option 2: Settings Panel (After Logging In)
1. Log in to Odysseus
2. Click **Settings** in the left menu
3. Find **General** → **Language**
4. Select **Español** from the dropdown
5. The entire app will update instantly

### 📋 Supported Languages

- 🇬🇧 **English** (en)
- 🇪🇸 **Español** (es)

### ✨ What Gets Translated

✅ Login page and auth forms
✅ Navigation menu and all buttons
✅ Chat interface
✅ Settings and preferences
✅ Documents, notes, tasks
✅ Calendar interface
✅ Error messages
✅ Status messages and notifications
✅ 250+ UI strings

### 🔧 Technical Details

**Files Added:**
- `static/js/i18n.js` — Main translation system
- `static/js/languageSwitcher.js` — UI component
- `static/js/loginI18n.js` — Login page handler
- `SPANISH_TRANSLATION_GUIDE.md` — Full documentation
- `INTEGRATION_GUIDE_LANGUAGE_SWITCHER.md` — Integration help

**Files Modified:**
- `static/app.js` — Added i18n imports
- `static/login.html` — Added language switcher
- `static/style.css` — Added styling

### 🌐 Browser Storage

Language preference is saved in your browser's localStorage:
- Persists across sessions
- Synced with login/session
- Stored as: `odysseus-language`

### 🔍 For Developers

**Use i18n in your code:**

```javascript
// Get translation
const text = window.i18nModule.t('chat.send');

// Switch language
window.i18nModule.setLanguage('es');

// Create language switcher UI
const switcher = window.languageSwitcher.createSwitcher();
```

**Add translations to HTML:**

```html
<button data-i18n="action.save">Save</button>
<input data-i18n-placeholder="chat.message_placeholder" />
```

### 📚 Documentation

- **[SPANISH_TRANSLATION_GUIDE.md](./SPANISH_TRANSLATION_GUIDE.md)** — Complete guide
- **[INTEGRATION_GUIDE_LANGUAGE_SWITCHER.md](./INTEGRATION_GUIDE_LANGUAGE_SWITCHER.md)** — Integration details

### 🎯 Next Steps

1. **Test it out!** Switch to Spanish on the login page
2. **Customize** — Add more languages by editing `i18n.js`
3. **Integrate** — Add language switcher to your custom UI components
4. **Extend** — Add more translation strings as needed

### ❓ Troubleshooting

**Language not changing?**
- Check browser console for errors
- Clear localStorage: `localStorage.removeItem('odysseus-language')`
- Hard refresh the page: Ctrl+Shift+R (or Cmd+Shift+R on Mac)

**Missing translations?**
- The system falls back to English automatically
- Add missing translations to `i18n.js`
- Restart the app to load new translations

**Want to contribute translations?**
- Edit `static/js/i18n.js`
- Add your language to the `translations` object
- Update `getAvailableLanguages()` method
- Submit a PR to the Odysseus repository!

### 🚀 Adding New Languages

To add French support, for example:

1. Open `static/js/i18n.js`
2. Add a `fr` section with French translations
3. Update the `getAvailableLanguages()` method
4. Restart the app
5. Language switcher will show French option!

Example:
```javascript
fr: {
  'login.username': 'Nom d\'utilisateur',
  'chat.send': 'Envoyer',
  // ... more translations
}

getAvailableLanguages() {
  return {
    en: 'English',
    es: 'Español',
    fr: 'Français'  // ← New!
  };
}
```

### 📞 Support

For issues or questions:
1. Check the full documentation files
2. Review the code comments
3. Check browser console for errors
4. Open an issue on GitHub

---

**Enjoy Odysseus en Español!** 🎉

¡Disfruta Odysseus en Español! 🎉
