// Shared test-only helpers: build and parse the visual editor's node descriptors.
import assert from 'node:assert/strict';

export const el = (tag, children = [], { attrs = {}, classes = [], style = {} } = {}) => ({
  type: 'element',
  tag,
  attrs,
  classes,
  style,
  children: typeof children === 'string' ? [{ type: 'text', text: children }] : children,
});

export const txt = (text) => ({ type: 'text', text });

const VOID_TAGS = new Set(['br', 'hr', 'img']);
const ENTITY_TEXT = { amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'", nbsp: ' ' };
const decodeEntities = (value) => value.replace(/&(amp|lt|gt|quot|#39|nbsp);/g, (_, name) => ENTITY_TEXT[name]);

/**
 * Parse the codec's own well-formed HTML output into the descriptor shape that
 * markdownVisualSync.domToVisualNodes produces from a live DOM. Test-only: it
 * supports exactly the markup markdownToVisualHtml emits.
 */
export function parseCodecHtml(html) {
  const root = { children: [] };
  const stack = [root];
  const tagRe = /<(\/?)([a-z0-9]+)((?:\s+[a-z-]+="[^"]*")*)\s*\/?>/g;
  let last = 0;
  let match;
  const pushText = (raw) => {
    if (raw) stack.at(-1).children.push(txt(decodeEntities(raw)));
  };
  while ((match = tagRe.exec(html))) {
    pushText(html.slice(last, match.index));
    last = tagRe.lastIndex;
    const [, closing, tag, rawAttrs] = match;
    if (closing) {
      assert.equal(stack.at(-1).tag, tag, `Mismatched closing tag: expected </${stack.at(-1).tag}> but found </${tag}> in ${html}`);
      stack.pop();
      continue;
    }
    const attrs = {};
    const style = {};
    let classes = [];
    for (const [, name, value] of rawAttrs.matchAll(/([a-z-]+)="([^"]*)"/g)) {
      const decoded = decodeEntities(value);
      if (name === 'class') classes = decoded.split(/\s+/).filter(Boolean);
      else if (name === 'style') {
        const align = decoded.match(/text-align:\s*([a-z]+)/);
        if (align) style.textAlign = align[1];
      } else attrs[name] = decoded;
    }
    const node = { type: 'element', tag, attrs, classes, style, children: [] };
    stack.at(-1).children.push(node);
    if (!VOID_TAGS.has(tag)) stack.push(node);
  }
  pushText(html.slice(last));
  assert.equal(stack.length, 1, `Unclosed tags remaining in ${html}`);
  return root.children;
}
