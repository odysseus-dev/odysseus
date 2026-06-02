import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createTranslationObserver,
  lookup,
  normalizeLocale,
  translateElement,
  translateRoot,
} from '../static/js/i18n-core.mjs';

class FakeElement {
  constructor(attrs = {}, children = []) {
    this.attrs = new Map(Object.entries(attrs));
    this.children = children;
    this.nodeType = 1;
    this.textContent = '';
  }

  hasAttribute(name) {
    return this.attrs.has(name);
  }

  getAttribute(name) {
    return this.attrs.get(name) ?? null;
  }

  setAttribute(name, value) {
    this.attrs.set(name, value);
  }

  querySelectorAll() {
    return this.children.filter((child) => child.attrs.size > 0);
  }
}

test('lookup falls back to the source string', () => {
  assert.equal(lookup({ Settings: 'Paramètres' }, 'Settings'), 'Paramètres');
  assert.equal(lookup({ Settings: 'Paramètres' }, 'Missing'), 'Missing');
});

test('normalizeLocale prefers exact matches then primary language matches', () => {
  const locales = ['ar', 'fr', 'pt-BR', 'sv'];

  assert.equal(normalizeLocale('pt-br', locales), 'pt-BR');
  assert.equal(normalizeLocale('fr-CA', locales), 'fr');
  assert.equal(normalizeLocale('unknown', locales), 'en');
});

test('translateElement updates text and common attributes', () => {
  const element = new FakeElement({
    'data-i18n': 'Sign In',
    'data-i18n-placeholder': 'Search memories…',
    'data-i18n-aria-label': 'Show password',
  });

  translateElement(element, {
    'Sign In': 'Se connecter',
    'Search memories…': 'Rechercher dans les souvenirs…',
    'Show password': 'Afficher le mot de passe',
  });

  assert.equal(element.textContent, 'Se connecter');
  assert.equal(element.getAttribute('placeholder'), 'Rechercher dans les souvenirs…');
  assert.equal(element.getAttribute('aria-label'), 'Afficher le mot de passe');
});

test('translateRoot includes the root element and descendants', () => {
  const child = new FakeElement({ 'data-i18n-title': 'Language' });
  const root = new FakeElement({ 'data-i18n': 'Settings' }, [child]);

  translateRoot(root, {
    Language: 'Langue',
    Settings: 'Paramètres',
  });

  assert.equal(root.textContent, 'Paramètres');
  assert.equal(child.getAttribute('title'), 'Langue');
});

test('createTranslationObserver translates added and retagged nodes', () => {
  let observer;
  class FakeMutationObserver {
    constructor(callback) {
      this.callback = callback;
      observer = this;
    }

    observe(target, options) {
      this.target = target;
      this.options = options;
    }
  }

  const documentRef = {
    body: new FakeElement(),
    defaultView: { MutationObserver: FakeMutationObserver },
  };
  const added = new FakeElement({ 'data-i18n': 'Sign In' });
  const retagged = new FakeElement({ 'data-i18n': 'Settings' });

  createTranslationObserver(documentRef, () => ({
    Settings: 'Paramètres',
    'Sign In': 'Se connecter',
  }));
  observer.callback([
    { type: 'childList', addedNodes: [added] },
    { type: 'attributes', target: retagged },
  ]);

  assert.deepEqual(observer.options.attributeFilter, [
    'data-i18n',
    'data-i18n-placeholder',
    'data-i18n-title',
    'data-i18n-aria-label',
  ]);
  assert.equal(added.textContent, 'Se connecter');
  assert.equal(retagged.textContent, 'Paramètres');
});
