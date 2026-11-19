import { API_BASE } from "../../application/documentState.js";

import { _escHtml } from "../../domain/documentKinds.js";

function _commitRecipient(input, sugg, email) {
    if (!input) return;
    const { confirmed } = _splitRecipientsAndFragment(input.value);
    // Preserve a single trailing space between commas for readability.
    const head = confirmed ? confirmed.replace(/\s+$/, '') + ' ' : '';
    input.value = head + email + ', ';
    if (sugg) sugg.style.display = 'none';
    input.focus();
    // Caret to end so the next keystroke lands in the right place.
    const end = input.value.length;
    try { input.setSelectionRange(end, end); } catch (_) {}
  }

async function _searchContacts(input, sugg) {
    if (!input || !sugg) return;
    const { fragment } = _splitRecipientsAndFragment(input.value);
    if (!fragment || fragment.length < 1) { sugg.style.display = 'none'; return; }
    const searchedFragment = fragment;
    _showContactSearchStatus(sugg, 'Searching contacts...', 'loading');
    try {
      const res = await fetch(`${API_BASE}/api/contacts/search?q=${encodeURIComponent(fragment)}`);
      const data = await res.json();
      // A later keystroke started another lookup while this one was in flight.
      if (_splitRecipientsAndFragment(input.value).fragment !== searchedFragment) return;
      if (!data.results || data.results.length === 0) {
        if (data.sync?.state === 'unavailable' || data.sync?.state === 'local') {
          _showContactSearchStatus(sugg, data.sync.message, data.sync.state);
        } else {
          sugg.style.display = 'none';
        }
        return;
      }
      // Already-entered emails in this field — skip in the dropdown so
      // users don't accidentally add the same person twice.
      const already = new Set(
        (input.value || '').split(',').map(s => {
          const m = s.match(/<([^>]+)>/);
          return (m ? m[1] : s).trim().toLowerCase();
        }).filter(Boolean)
      );
      sugg.innerHTML = '';
      sugg.dataset.navStarted = '0';
      let count = 0;
      for (const c of data.results) {
        for (const em of (c.emails || [])) {
          if (already.has(em.toLowerCase())) continue;
          const item = document.createElement('div');
          item.className = 'contact-suggestion';
          item.setAttribute('role', 'option');
          item.setAttribute('aria-selected', 'false');
          item.innerHTML = `<span class="contact-name">${_escHtml(c.name)}</span><span class="contact-email">${_escHtml(em)}</span>`;
          item.addEventListener('mouseenter', () => {
            sugg.dataset.navStarted = '1';
            sugg.querySelectorAll('.contact-suggestion').forEach(it => {
              it.classList.toggle('active', it === item);
              it.setAttribute('aria-selected', it === item ? 'true' : 'false');
            });
          });
          // mousedown fires before blur so the click doesn't get lost
          item.addEventListener('mousedown', (e) => { e.preventDefault(); _commitRecipient(input, sugg, em); });
          item.addEventListener('click', (e) => { e.preventDefault(); _commitRecipient(input, sugg, em); });
          sugg.appendChild(item);
          count += 1;
        }
      }
      if (count === 0) { sugg.style.display = 'none'; return; }
      sugg.style.display = '';
    } catch (e) {
      if (_splitRecipientsAndFragment(input.value).fragment !== searchedFragment) return;
      _showContactSearchStatus(sugg, 'Unable to search contacts.', 'unavailable');
    }
  }

function _showContactSearchStatus(sugg, message, state = '') {
    sugg.innerHTML = '';
    const notice = document.createElement('div');
    notice.className = `contact-suggestion-status${state ? ` is-${state}` : ''}`;
    notice.setAttribute('role', 'status');
    notice.textContent = message;
    sugg.appendChild(notice);
    sugg.style.display = '';
  }

function _splitRecipientsAndFragment(rawValue) {
    const cut = (rawValue || '').lastIndexOf(',');
    if (cut < 0) return { confirmed: '', fragment: (rawValue || '').trimStart() };
    return {
      confirmed: rawValue.slice(0, cut + 1).trimStart(),
      fragment: rawValue.slice(cut + 1).trimStart(),
    };
  }

export function _wireRecipientAutocomplete(inputId, suggId) {
    const input = document.getElementById(inputId);
    const sugg = document.getElementById(suggId);
    if (!input || !sugg) return;
    let timer = null;
    input.addEventListener('input', () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => _searchContacts(input, sugg), 150);
    });
    input.addEventListener('blur', () => {
      setTimeout(() => { sugg.style.display = 'none'; }, 200);
    });
    input.addEventListener('keydown', (e) => {
      const open = sugg.style.display !== 'none';
      const items = open ? sugg.querySelectorAll('.contact-suggestion') : [];
      const active = open ? sugg.querySelector('.contact-suggestion.active') : null;
      let idx = active ? Array.from(items).indexOf(active) : -1;
      const setActive = (nextIdx) => {
        items.forEach((it, i) => {
          const on = i === nextIdx;
          it.classList.toggle('active', on);
          it.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        if (items[nextIdx]) {
          items[nextIdx].scrollIntoView({ block: 'nearest' });
        }
      };
      if (open && e.key === 'ArrowDown') {
        e.preventDefault();
        if (!items.length) return;
        if (sugg.dataset.navStarted !== '1') {
          idx = Math.max(0, idx);
          sugg.dataset.navStarted = '1';
        } else {
          idx = Math.min(items.length - 1, idx + 1);
        }
        setActive(idx);
      } else if (open && e.key === 'ArrowUp') {
        e.preventDefault();
        if (!items.length) return;
        sugg.dataset.navStarted = '1';
        idx = Math.max(0, idx - 1);
        setActive(idx);
      } else if (e.key === 'Enter') {
        // If a suggestion is highlighted, commit it. Otherwise — if the
        // current fragment already looks like a complete email — commit
        // the raw text so users who type a brand-new address don't have
        // to add the comma themselves.
        if (active) {
          e.preventDefault();
          const em = active.querySelector('.contact-email')?.textContent?.trim();
          if (em) _commitRecipient(input, sugg, em);
        } else {
          const { fragment } = _splitRecipientsAndFragment(input.value);
          if (/^[^@\s,]+@[^@\s,]+\.[^@\s,]+$/.test(fragment.trim())) {
            e.preventDefault();
            _commitRecipient(input, sugg, fragment.trim());
          }
        }
      } else if (e.key === 'Tab' && active) {
        e.preventDefault();
        const em = active.querySelector('.contact-email')?.textContent?.trim();
        if (em) _commitRecipient(input, sugg, em);
      } else if (e.key === 'Escape') {
        sugg.style.display = 'none';
      } else if (e.key === ',' || (e.key === ' ' && input.value.trim().endsWith(','))) {
        // Typing a comma directly also accepts a highlighted suggestion.
        if (active) {
          e.preventDefault();
          const em = active.querySelector('.contact-email')?.textContent?.trim();
          if (em) _commitRecipient(input, sugg, em);
        }
      }
    });
  }
