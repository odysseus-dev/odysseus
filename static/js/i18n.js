/**
 * i18n - 国际化模块
 * 方案 B：data-i18n + 动态文本映射
 *
 * 核心：加载 en.json（源）和目标语言 JSON，自动构建英文→目标语言映射
 * 添加新语言只需：1) 新建 xx.json  2) 语言选择器加选项
 */

const I18N = {
  currentLang: localStorage.getItem('odysseus-lang') || 'zh',
  translations: {},    // 当前语言翻译数据
  enTranslations: {},  // 英文翻译数据（构建映射用）
  _loaded: false,
  _callbacks: [],
  _textMap: null,      // 英文→目标语言文本映射缓存
  _origTexts: [],      // 原始文本节点记录，用于恢复

  async init() {
    // 优先使用同步预加载的翻译
    if (window._i18n && window._i18n.t && Object.keys(window._i18n.t).length > 0) {
      this.translations = window._i18n.t;
      this.currentLang = window._i18n.lang;
    } else {
      await this.loadTranslations(this.currentLang);
    }

    // 始终加载英文翻译（构建映射源）
    await this.loadEnTranslations();

    this._loaded = true;

    // 构建文本映射
    this._textMap = this._buildTextMap();

    this.apply();
    this._callbacks.forEach(cb => cb(this.translations));
    this._callbacks = [];
    this._observeDOM();
    return this.translations;
  },

  ready(callback) {
    if (this._loaded) {
      callback && callback(this.translations);
      return Promise.resolve(this.translations);
    }
    return new Promise(resolve => {
      this._callbacks.push((t) => { callback && callback(t); resolve(t); });
    });
  },

  async loadTranslations(lang) {
    try {
      const response = await fetch(`/static/locales/${lang}.json?v=${Date.now()}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      this.translations = await response.json();
      this.currentLang = lang;
      console.log(`[i18n] Loaded: ${lang}`);
    } catch (e) {
      console.warn('[i18n] Failed to load translations:', e);
      this.translations = {};
    }
  },

  async loadEnTranslations() {
    if (Object.keys(this.enTranslations).length > 0) return;
    try {
      const response = await fetch(`/static/locales/en.json?v=${Date.now()}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      this.enTranslations = await response.json();
      console.log('[i18n] Loaded: en (base)');
    } catch (e) {
      console.warn('[i18n] Failed to load en.json:', e);
      this.enTranslations = {};
    }
  },

  t(key, fallback) {
    const keys = key.split('.');
    let value = this.translations;
    for (const k of keys) {
      value = value?.[k];
      if (value === undefined) break;
    }
    return value || fallback || key;
  },

  async setLang(lang) {
    if (lang === this.currentLang) return;

    // 恢复原始文本
    this._restoreOriginals();

    await this.loadTranslations(lang);
    this.currentLang = lang;
    localStorage.setItem('odysseus-lang', lang);
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : lang;

    // 重新构建映射
    this._textMap = this._buildTextMap();
    this._origTexts = [];

    this.apply();

    window.dispatchEvent(new CustomEvent('i18n:languageChanged', {
      detail: { lang, translations: this.translations }
    }));
    console.log(`[i18n] Switched to: ${lang}`);
  },

  /* ═══ 动态构建文本映射：en.json 值 → 目标语言值 ═══ */
  _buildTextMap() {
    if (this.currentLang === 'en') return {};
    const map = {};
    const enFlat = {};
    const zhFlat = {};

    const flatten = (obj, prefix, target) => {
      for (const [k, v] of Object.entries(obj)) {
        const key = prefix ? `${prefix}.${k}` : k;
        if (typeof v === 'string') {
          target[key] = v;
        } else if (typeof v === 'object' && v !== null) {
          flatten(v, key, target);
        }
      }
    };

    flatten(this.enTranslations, '', enFlat);
    flatten(this.translations, '', zhFlat);

    // 按 key 匹配：en 的值 → 目标语言的值
    for (const key of Object.keys(enFlat)) {
      if (zhFlat[key] && enFlat[key] !== zhFlat[key]) {
        map[enFlat[key]] = zhFlat[key];
      }
    }

    console.log(`[i18n] Text map built: ${Object.keys(map).length} entries`);
    return map;
  },

  apply(root = document) {
    // 1. data-i18n 属性翻译（始终生效）
    root.querySelectorAll('[data-i18n]').forEach(el => {
      const key = el.dataset.i18n;
      const text = this.t(key);
      if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
        if (el.placeholder) el.placeholder = text;
      } else {
        el.textContent = text;
      }
    });

    root.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      el.placeholder = this.t(el.dataset.i18nPlaceholder);
    });

    root.querySelectorAll('[data-i18n-title]').forEach(el => {
      el.title = this.t(el.dataset.i18nTitle);
    });

    root.querySelectorAll('[data-i18n-html]').forEach(el => {
      el.innerHTML = this.t(el.dataset.i18nHtml);
    });

    // 2. 文本映射自动翻译
    if (this.currentLang !== 'en' && this._textMap && Object.keys(this._textMap).length > 0) {
      this._applyTextMap(root);
    }
  },

  /* 扫描 DOM 文本节点并替换 */
  _applyTextMap(root) {
    const map = this._textMap;
    if (!map) return;

    // 收集所有需要翻译的叶子级文本容器
    const leafSelectors =
      '.settings-label, .settings-nav-item, .admin-toggle-sub, .vis-label, ' +
      '.section-title-label, .list-item, .memory-toolbar-btn, .admin-tab, ' +
      '.section-header-btn, .theme-io-btn, .color-row label, ' +
      'h2 > span, h4 > span, .vis-hint, .settings-fallback-add, ' +
      'button, label, option, .grow';

    // 1. 叶子级文本节点替换（最高优先级，精确匹配）
    root.querySelectorAll(leafSelectors).forEach(el => {
      if (el.dataset.i18n || el.dataset.i18nHtml) return;
      this._translateTextNodes(el, map);
    });

    // 2. 大容器级 innerHTML 替换（处理含 HTML 子元素的文本，如 <code>）
    const htmlContainers = root.querySelectorAll('.admin-toggle-sub, .vis-label');
    htmlContainers.forEach(el => {
      if (el.dataset.i18n || el.dataset.i18nHtml) return;
      if (el._i18nHtmlSaved) return;
      // 跳过已经通过文本节点处理过的
      const html = el.innerHTML;
      if (this._tryInnerHtmlMap(el, html, map)) return;
    });

    // 3. 补充：h2/h4 直接文本（无 span 包裹的情况）
    root.querySelectorAll('h2, h4').forEach(el => {
      if (el.dataset.i18n || el.dataset.i18nHtml) return;
      if (el.querySelector('[data-i18n]')) return; // 子元素已处理
      this._translateTextNodes(el, map);
    });
  },

  /* 翻译元素内的文本节点 */
  _translateTextNodes(el, map) {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null, false);
    const textNodes = [];
    while (walker.nextNode()) textNodes.push(walker.currentNode);

    textNodes.forEach(node => {
      if (node._i18nSaved) return;
      const text = node.textContent.trim();
      if (!text) return;

      // 精确匹配
      if (map[text]) {
        this._origTexts.push({ node, original: node.textContent });
        node._i18nSaved = true;
        node.textContent = node.textContent.replace(text, map[text]);
        return;
      }

      // 贪婪匹配：找到 map 中最长的 key 被包含在 text 中
      let matched = false;
      for (const [enText, zhText] of Object.entries(map)) {
        if (enText.length < 4) continue; // 跳过太短的 key
        if (text.includes(enText)) {
          this._origTexts.push({ node, original: node.textContent });
          node._i18nSaved = true;
          node.textContent = node.textContent.replace(enText, zhText);
          matched = true;
          break;
        }
      }
    });
  },

  /* 尝试 innerHTML 级别的替换（处理含 HTML 标签的文本） */
  _tryInnerHtmlMap(el, html, map) {
    let newHtml = html;
    let changed = false;

    for (const [enText, zhText] of Object.entries(map)) {
      if (enText.length < 4) continue;
      if (newHtml.includes(enText)) {
        newHtml = newHtml.replace(enText, zhText);
        changed = true;
      }
    }

    if (changed) {
      el._i18nHtmlSaved = true;
      this._origTexts.push({ el, originalHtml: html, type: 'html' });
      el.innerHTML = newHtml;
    }
    return changed;
  },

  /* 恢复原始文本 */
  _restoreOriginals() {
    this._origTexts.forEach(item => {
      if (item.type === 'html' && item.el) {
        item.el.innerHTML = item.originalHtml;
        delete item.el._i18nHtmlSaved;
      } else if (item.node && item.node.parentNode) {
        item.node.textContent = item.original;
        delete item.node._i18nSaved;
      }
    });
    this._origTexts = [];
  },

  _observeDOM() {
    if (typeof MutationObserver === 'undefined') return;

    const observer = new MutationObserver((mutations) => {
      let shouldApply = false;

      mutations.forEach(mutation => {
        mutation.addedNodes.forEach(node => {
          if (node.nodeType === Node.ELEMENT_NODE) {
            if (node.hasAttribute &&
                (node.hasAttribute('data-i18n') ||
                 node.hasAttribute('data-i18n-placeholder') ||
                 node.hasAttribute('data-i18n-title') ||
                 node.hasAttribute('data-i18n-html') ||
                 node.querySelector('[data-i18n], [data-i18n-placeholder], [data-i18n-title], [data-i18n-html]'))) {
              shouldApply = true;
            }
            if (node.matches && node.matches(
              '#settings-panel, .admin-card, h2, h4, .settings-label, .admin-toggle-sub, ' +
              '.vis-label, .section-title, .list-item, button, label, option'
            )) {
              shouldApply = true;
            }
            if (node.querySelector) {
              const sub = node.querySelector(
                '#settings-panel, .admin-card, h2, h4, .settings-label, .admin-toggle-sub, button, label'
              );
              if (sub) shouldApply = true;
            }
          }
        });
      });

      if (shouldApply) this.apply();
    });

    observer.observe(document.body, { childList: true, subtree: true });
  },

  tf(key, params = {}, fallback) {
    let text = this.t(key, fallback);
    Object.keys(params).forEach(k => {
      text = text.replace(new RegExp(`{${k}}`, 'g'), params[k]);
    });
    return text;
  }
};

window.t = (key, fallback) => {
  if (window._i18n && window._i18n.t) {
    const keys = key.split('.');
    let val = window._i18n.t;
    for (const k of keys) { val = val?.[k]; if (val === undefined) break; }
    if (val !== undefined) return val;
  }
  return I18N.t(key, fallback);
};
window.tf = (key, params, fallback) => I18N.tf(key, params, fallback);
window.I18N = I18N;

I18N.init();

export default I18N;
