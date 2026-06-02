import assert from 'node:assert/strict';
import test from 'node:test';

import {
  lookup,
  normalizeLocale,
  translateElement,
} from '../static/js/i18n-core.mjs';

class FakeElement {
  constructor(attrs = {}) {
    this.attrs = new Map(Object.entries(attrs));
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
