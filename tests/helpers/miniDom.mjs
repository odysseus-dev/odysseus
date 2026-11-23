// Minimal HTML parser for node tests of DOM-walking code. Produces nodes with
// nodeType, nodeName, nodeValue, childNodes, getAttribute and outerHTML, which
// is all features/notes/domain/markdownSerializer.js reads. It understands the
// well-formed markup mdToHtml emits; it is not a general HTML5 parser.
const VOID = new Set(['area', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'wbr']);
const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'", apos: "'", nbsp: ' ' , colon: ':', tab: '\t', newline: '\n' };
const decode = s => s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
  if (ENTITIES[e.toLowerCase()] !== undefined) return ENTITIES[e.toLowerCase()];
  if (e[0] === '#') return String.fromCodePoint(e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10));
  return m;
});
const escText = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const escAttr = s => escText(s).replace(/"/g, '&quot;');

class Text {
  constructor(value) { this.nodeType = 3; this.nodeName = '#text'; this.nodeValue = value; this.childNodes = []; }
  get outerHTML() { return escText(this.nodeValue); }
}

class Element {
  constructor(tag, attrs = []) { this.nodeType = 1; this.nodeName = tag.toUpperCase(); this.attrs = attrs; this.childNodes = []; }
  get tagName() { return this.nodeName; }
  get previousSibling() { const kids = this.parentNode?.childNodes || []; return kids[kids.indexOf(this) - 1] || null; }
  get attributes() { return this.attrs.map(([name, value]) => ({ name, value })); }
  getAttribute(name) { const hit = this.attrs.find(([k]) => k === name.toLowerCase()); return hit ? hit[1] : null; }
  removeAttribute(name) { this.attrs = this.attrs.filter(([k]) => k !== name.toLowerCase()); }
  remove() { if (this.parentNode) this.parentNode.childNodes = this.parentNode.childNodes.filter(n => n !== this); this.parentNode = null; }
  querySelectorAll(selector) {
    if (selector !== '*') throw new Error(`miniDom only supports querySelectorAll('*'), got ${selector}`);
    const out = [];
    const walk = node => node.childNodes.forEach(child => { if (child.nodeType === 1) { out.push(child); walk(child); } });
    walk(this);
    return out;
  }
  set innerHTML(html) { this.childNodes = parseHtml(html).childNodes; this.childNodes.forEach(child => { child.parentNode = this; }); }
  setAttribute(name, value) { const hit = this.attrs.find(([k]) => k === name.toLowerCase()); if (hit) hit[1] = String(value); else this.attrs.push([name.toLowerCase(), String(value)]); }
  get textContent() { return this.childNodes.map(n => (n.nodeType === 3 ? n.nodeValue : n.textContent)).join(''); }
  get innerHTML() { return this.childNodes.map(n => n.outerHTML).join(''); }
  get outerHTML() {
    const tag = this.nodeName.toLowerCase();
    const attrs = this.attrs.map(([k, v]) => ` ${k}="${escAttr(v)}"`).join('');
    return VOID.has(tag) ? `<${tag}${attrs}>` : `<${tag}${attrs}>${this.innerHTML}</${tag}>`;
  }
}

export function parseHtml(html) {
  const root = new Element('#root');
  const stack = [root];
  // Each alternative inside a tag starts with a different character (a quote
  // or anything else but `>`), so a long run of attributes cannot backtrack.
  const re = /<!--[\s\S]*?-->|<\/([a-zA-Z][\w-]*)\s*>|<([a-zA-Z][\w-]*)((?:"[^"]*"|'[^']*'|[^'">])*)>|([^<]+|<)/g;
  let m;
  while ((m = re.exec(html))) {
    const top = stack[stack.length - 1];
    if (m[0].startsWith('<!--')) continue;
    if (m[1]) {
      const tag = m[1].toUpperCase();
      const at = stack.map(n => n.nodeName).lastIndexOf(tag);
      if (at > 0) stack.length = at;
      continue;
    }
    if (m[2]) {
      const selfClosing = /\/\s*$/.test(m[3]);
      const attrs = [];
      for (const a of m[3].replace(/\/\s*$/, '').matchAll(/([^\s=>\/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) attrs.push([a[1].toLowerCase(), decode(a[2] ?? a[3] ?? a[4] ?? '')]);
      const el = new Element(m[2], attrs);
      el.parentNode = top;
      top.childNodes.push(el);
      if (!VOID.has(m[2].toLowerCase()) && !selfClosing) stack.push(el);
      continue;
    }
    const text = new Text(decode(m[4]));
    text.parentNode = top;
    top.childNodes.push(text);
  }
  return root;
}

/** A `document` whose <template> parses with parseHtml, for code that sanitizes on an inert template. */
export function templateDocument() {
  return {
    createElement(tag) {
      if (tag !== 'template') throw new Error(`unsupported element: ${tag}`);
      const content = new Element('#root');
      return { content, set innerHTML(html) { content.innerHTML = html; }, get innerHTML() { return content.innerHTML; } };
    },
  };
}
