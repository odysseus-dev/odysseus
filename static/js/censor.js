// static/js/censor.js
/**
 * Sensitive Information Censor Module
 * Detects emails, passwords, API keys, tokens, etc. in chat responses
 * and blurs them. Click to reveal individual items.
 */

let _enabled = true;
let _observer = null;
const PREF_KEY = 'odysseus-sensitive-blur';
export const _prefEnabled = () => {
  try {
    return localStorage.getItem(PREF_KEY) === 'on';
  } catch (_) {
    return false;
  }
};

// Patterns that indicate sensitive data
const PATTERNS = [
  // Emails
  { re: /\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b/g, label: 'email' },
  // API key prefixes (common services)
  { re: /\b(sk-[a-zA-Z0-9]{20,}|pk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{36,}|gho_[a-zA-Z0-9]{36,}|glpat-[a-zA-Z0-9\-_]{20,}|xox[bpras]-[a-zA-Z0-9\-]{10,}|npm_[a-zA-Z0-9]{36,}|AKIA[A-Z0-9]{12,})\b/g, label: 'api-key' },
  // Bearer tokens
  { re: /Bearer\s+[A-Za-z0-9._\-]{20,}/g, label: 'token' },
  // Generic tokens/secrets in key=value or key: value patterns
  // Credentials with delimiters (key: value, key=value, key  value)
  { re: /(?:password|passwd|secret|api[_\-]?key|access[_\-]?token|auth[_\-]?token|private[_\-]?key|client[_\-]?secret)[\s]*[:=]\s*["']?[^\s"'<]{4,}["']?/gi, label: 'credential' },
  // Credentials in tabular/label-value format (Password    xyzABC123)
  { re: /(?:password|passwd|secret|api[_\-]?key|access[_\-]?token|auth[_\-]?token|private[_\-]?key|client[_\-]?secret)\s{2,}[^\s<]{4,}/gi, label: 'credential' },
  // Value after a line starting with password-like label
  { re: /(?:^|\n)\s*(?:password|passwd|secret|api[_\-]?key|token|private[_\-]?key)[\t ]*\n\s*([^\s<]{4,})/gim, label: 'credential' },
  // SSH / PEM private keys (inline)
  { re: /-----BEGIN\s[\w\s]*PRIVATE KEY-----[\s\S]*?-----END\s[\w\s]*PRIVATE KEY-----/g, label: 'private-key' },
  // Long hex strings (32+ chars) that look like hashes/tokens
  { re: /\b[0-9a-f]{32,}\b/gi, label: 'hash' },
  // JWT tokens (three dot-separated base64 segments)
  { re: /\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b/g, label: 'jwt' },
  // IP addresses with ports (internal networks)
  { re: /\b(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}(?::\d+)?\b/g, label: 'internal-ip' },
];

export function init() {
  // Load enabled state from feature flags
  _loadState();
  window.addEventListener('odysseus-sensitive-blur-change', (e) => {
    setEnabled(e.detail?.enabled !== false);
  });
  // Set up click handler for reveals (delegated)
  document.addEventListener('click', (e) => {
    const el = e.target.closest('.censored-item');
    if (!el) return;
    e.preventDefault();
    e.stopPropagation();
    el.classList.toggle('revealed');
  });
}

function _loadState() {
  // Check admin feature flag
  fetch('/api/auth/features', { credentials: 'same-origin' })
    .then(r => r.json())
    .then(features => {
      _enabled = features.sensitive_filter !== false && _prefEnabled();
      // Start observer after loading state
      _startObserver();
    })
    .catch(() => {
      // Default: enabled
      _enabled = _prefEnabled();
      _startObserver();
    });
}

function _startObserver() {
  if (_observer) return;
  // Observe chat-history, compare panes, and split panes for new messages
  _observer = new MutationObserver((mutations) => {
    if (!_enabled) return;
    for (const mutation of mutations) {
      for (const node of mutation.addedNodes) {
        if (node.nodeType !== 1) continue;
        // Process any .body elements within newly added nodes
        if (node.classList && node.classList.contains('body')) {
          _scheduleProcess(node);
        } else if (node.querySelectorAll) {
          node.querySelectorAll('.msg .body, .msg-ai .body').forEach(b => _scheduleProcess(b));
        }
      }
    }
  });

  // Observe the entire main area for new messages
  const targets = [
    document.getElementById('chat-container'),
    document.getElementById('chat-history'),
  ].filter(Boolean);

  targets.forEach(t => {
    _observer.observe(t, { childList: true, subtree: true });
  });
}

// Debounce processing — content may still be streaming
const _pending = new WeakSet();
function _scheduleProcess(el) {
  if (_pending.has(el)) return;
  _pending.add(el);
  // Wait for streaming to settle — process after a short delay
  // Re-process periodically during streaming
  let attempts = 0;
  const maxAttempts = 30;
  const interval = setInterval(() => {
    _processElement(el);
    attempts++;
    if (attempts >= maxAttempts) clearInterval(interval);
  }, 2000);
  // Also process once immediately (catches non-streaming content)
  setTimeout(() => _processElement(el), 100);
  // Final pass after streaming likely done
  setTimeout(() => {
    clearInterval(interval);
    _processElement(el);
    _pending.delete(el);
  }, 60000);
}

// Labels that indicate the NEXT value should be censored
const SENSITIVE_LABELS = /^(?:password|passwd|secret|api[_\-]?key|access[_\-]?token|auth[_\-]?token|private[_\-]?key|client[_\-]?secret|token|credentials?)$/i;

function _processElement(el) {
  if (!_enabled || !el) return;
  if (el.closest && el.closest('.setup-guide-no-censor')) return;

  // --- Pass 1: Pattern-based censoring on text nodes ---
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
  const textNodes = [];
  let node;
  while ((node = walker.nextNode())) {
    if (node.parentElement.closest('.setup-guide-no-censor')) continue;
    if (node.parentElement.closest('pre:not(.censored-item), .censored-item')) continue;
    textNodes.push(node);
  }

  for (const textNode of textNodes) {
    const text = textNode.textContent;
    if (!text || text.trim().length < 4) continue;

    const matches = [];
    for (const pattern of PATTERNS) {
      pattern.re.lastIndex = 0;
      let m;
      while ((m = pattern.re.exec(text)) !== null) {
        matches.push({ start: m.index, end: m.index + m[0].length, text: m[0], label: pattern.label });
      }
    }
    if (matches.length === 0) continue;

    matches.sort((a, b) => a.start - b.start);
    const deduped = [matches[0]];
    for (let i = 1; i < matches.length; i++) {
      const prev = deduped[deduped.length - 1];
      if (matches[i].start < prev.end) {
        if (matches[i].end > prev.end) prev.end = matches[i].end;
      } else {
        deduped.push(matches[i]);
      }
    }

    const frag = document.createDocumentFragment();
    let lastIdx = 0;
    for (const match of deduped) {
      if (match.start > lastIdx) {
        frag.appendChild(document.createTextNode(text.slice(lastIdx, match.start)));
      }
      const span = document.createElement('span');
      span.className = 'censored-item';
      span.dataset.type = match.label;
      span.title = 'Click to reveal ' + match.label;
      span.textContent = match.text;
      frag.appendChild(span);
      lastIdx = match.end;
    }
    if (lastIdx < text.length) {
      frag.appendChild(document.createTextNode(text.slice(lastIdx)));
    }
    textNode.parentNode.replaceChild(frag, textNode);
  }

  // --- Pass 2: Context-aware label/value censoring ---
  // Finds elements where text matches a sensitive label, then censors
  // the adjacent sibling or next text content as a value.
  _contextCensor(el);
}

function _contextCensor(el) {
  // Strategy 1: Walk all elements looking for sensitive labels
  const allElements = el.querySelectorAll('td, th, dt, dd, span, strong, b, em, li, p, div');
  for (let i = 0; i < allElements.length; i++) {
    const elem = allElements[i];
    if (elem.closest('.setup-guide-no-censor')) continue;
    if (elem.closest('.censored-item, pre')) continue;
    const txt = (elem.textContent || '').trim();
    if (!SENSITIVE_LABELS.test(txt)) continue;

    // Found a label — censor value via multiple strategies
    let censored = false;

    // A) Next text sibling node (e.g. <strong>Password</strong> value123)
    let sibling = elem.nextSibling;
    while (sibling && !censored) {
      if (sibling.nodeType === 3) { // text node
        const val = sibling.textContent.trim();
        if (val.length >= 4 && !SENSITIVE_LABELS.test(val)) {
          const span = document.createElement('span');
          span.className = 'censored-item';
          span.dataset.type = 'credential';
          span.title = 'Click to reveal credential';
          span.textContent = sibling.textContent;
          sibling.parentNode.replaceChild(span, sibling);
          censored = true;
        }
      } else if (sibling.nodeType === 1 && !sibling.closest('.censored-item')) {
        // Element sibling — censor its text
        const val = sibling.textContent.trim();
        if (val.length >= 4 && !SENSITIVE_LABELS.test(val)) {
          _censorAllText(sibling);
          censored = true;
        }
      }
      sibling = censored ? null : sibling.nextSibling;
    }

    // B) Parent's next element sibling (for <td>/<dd> pairs)
    if (!censored) {
      const parent = elem.parentElement;
      if (parent) {
        const nextEl = parent.nextElementSibling;
        if (nextEl && !nextEl.closest('.censored-item')) {
          const val = nextEl.textContent.trim();
          if (val.length >= 2 && !SENSITIVE_LABELS.test(val)) {
            _censorAllText(nextEl);
            censored = true;
          }
        }
      }
    }

    // C) Same parent, next text node after this element
    if (!censored && elem.parentElement) {
      const parent = elem.parentElement;
      let found = false;
      for (let c = 0; c < parent.childNodes.length; c++) {
        const child = parent.childNodes[c];
        if (child === elem) { found = true; continue; }
        if (!found) continue;
        if (child.nodeType === 3 && child.textContent.trim().length >= 4) {
          const val = child.textContent.trim();
          if (!SENSITIVE_LABELS.test(val)) {
            const span = document.createElement('span');
            span.className = 'censored-item';
            span.dataset.type = 'credential';
            span.title = 'Click to reveal credential';
            span.textContent = child.textContent;
            child.parentNode.replaceChild(span, child);
            break;
          }
        }
      }
    }
  }

  // Strategy 2: Full-text scan for label-value patterns across lines
  // Get the full text, find patterns like "Password\n  value" or "Password: value"
  const fullText = el.textContent || '';
  const labelValueRe = /(?:password|passwd|secret|api[_\-]?key|access[_\-]?token|private[_\-]?key|client[_\-]?secret|token|auth[_\-]?token)\s*[:\s]\s*(\S{4,})/gi;
  let m;
  while ((m = labelValueRe.exec(fullText)) !== null) {
    const value = m[1];
    // Find and censor this value string in text nodes
    _censorValueInElement(el, value);
  }
}

function _censorValueInElement(el, value) {
  if (!value || value.length < 4) return;
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
  let node;
  while ((node = walker.nextNode())) {
    if (node.parentElement.closest('.setup-guide-no-censor')) continue;
    if (node.parentElement.closest('pre:not(.censored-item), .censored-item')) continue;
    const idx = node.textContent.indexOf(value);
    if (idx < 0) continue;
    // Split text node and wrap the value
    const before = node.textContent.slice(0, idx);
    const after = node.textContent.slice(idx + value.length);
    const frag = document.createDocumentFragment();
    if (before) frag.appendChild(document.createTextNode(before));
    const span = document.createElement('span');
    span.className = 'censored-item';
    span.dataset.type = 'credential';
    span.title = 'Click to reveal credential';
    span.textContent = value;
    frag.appendChild(span);
    if (after) frag.appendChild(document.createTextNode(after));
    node.parentNode.replaceChild(frag, node);
    return; // One replacement per call to avoid walker issues
  }
}

function _censorAllText(el) {
  // Wrap all text content in a censored span
  if (el.querySelector('.censored-item')) return;
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
  const nodes = [];
  let n;
  while ((n = walker.nextNode())) {
    if (n.parentElement.closest('.setup-guide-no-censor')) continue;
    if (n.parentElement.closest('.censored-item, pre')) continue;
    if (n.textContent.trim().length >= 2) nodes.push(n);
  }
  for (const tn of nodes) {
    const span = document.createElement('span');
    span.className = 'censored-item';
    span.dataset.type = 'credential';
    span.title = 'Click to reveal credential';
    span.textContent = tn.textContent;
    tn.parentNode.replaceChild(span, tn);
  }
}

/** Manually censor a specific element (for dynamically loaded content) */
export function censorElement(el) {
  if (!_enabled) return;
  _processElement(el);
}

/** Toggle censoring on/off (client-side) */
export function setEnabled(enabled) {
  _enabled = enabled;
  if (!enabled) {
    // Reveal all currently censored items
    document.querySelectorAll('.censored-item').forEach(el => el.classList.add('revealed'));
  } else {
    document.querySelectorAll('.censored-item').forEach(el => el.classList.remove('revealed'));
  }
}

export function isEnabled() {
  return _enabled;
}

const censorModule = { init, censorElement, setEnabled, isEnabled };

export default censorModule;
