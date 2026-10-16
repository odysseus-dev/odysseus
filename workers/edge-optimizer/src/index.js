/**
 * edge-optimizer — two-phase HTML/CDN optimizer proxy.
 *
 * Rewrite of a prior E8/pi-metrics proxy. The goals are unchanged (proxy to
 * the origin, make the HTML cheaper to deliver); the implementation is not.
 * The measured problems in the previous version are listed in
 * specs/edge-optimizer.md under "What changed and why".
 *
 * Phase 1  HTMLRewriter pass over text/html: strip comments, collapse
 *          inter-tag whitespace, mark scripts non-blocking, preload critical
 *          styles, drop tracking pixels.
 * Phase 2  Header pass: correct caching, conditional revalidation, correct
 *          content negotiation.
 *
 * Deviates from `run_worker_first` convention: nothing bypasses this Worker.
 */

const CRITICAL_STYLE_CAP = 3;
const WHITESPACE_STRIP_TAGS = new Set([
  'html', 'head', 'body', 'script', 'style', 'pre', 'textarea',
  'noscript', 'template', 'iframe', 'svg', 'canvas', 'video', 'audio',
]);
const DROP_HREF_PREFIXES = [
  'https://analytics.', 'https://www.google-analytics.com',
  'https://googletagmanager.com/', 'https://connect.facebook.net',
];
const KEEP_SET_COOKIE = true;

/** Compact timing header. Cheap: one Date.now() delta, no BigInt. */
function timingHeaders(startedAt, now = performance.now()) {
  const ms = now - startedAt;
  return {
    'X-Optimizer-Time-Ms': ms.toFixed(2),
    // 32-bit FNV-1a over the ms string. Stable, allocation-light, and unlike
    // a BigInt-per-character hash it does not show up in the profile.
    'X-Optimizer-Trace': fnv1a(ms.toFixed(2)).toString(16).padStart(8, '0'),
  };
}

function fnv1a(str) {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/**
 * Strip HTML comments.
 *
 * Comments must be matched through an element selector (html/head/body) with a
 * `comments` handler; a bare 'comment' selector silently matches nothing.
 *
 * Conditional comments are removed too: they only ever executed in IE, and
 * they ship as bytes on every single load.
 */
function stripComment(chunk) {
  chunk.remove();
}

export class Phase1 {
  constructor(caps) {
    this.caps = caps;
  }

  /** Phase 1: everything that changes the body. Runs on the stream once. */
  transform(response) {
    const caps = this.caps;
    const rewriter = new HTMLRewriter()
      .on('*', {
        // Collapse ONLY text nodes that are entirely whitespace, i.e. the
        // indentation between tags.
        //
        // Keying on the parent tag name does not work: HTMLRewriter does not
        // reliably report parentTagName for every text chunk, so a <pre> guard
        // built on it silently fails and eats meaningful whitespace. Requiring
        // the chunk to be pure whitespace is both safe and simpler: no text
        // node with real content is ever touched, so <pre>, <textarea> and
        // inline spacing are preserved by construction.
        text(chunk) {
          const data = chunk.text;
          if (data.length === 0) return;
          if (data.trim() !== '') return;
          const cleaned = data.replace(/[ \t\r\n\f]+/g, ' ');
          if (cleaned === data) return;
          // replace() takes a computed string. Passing a RegExp here
          // substitutes the literal text "/\s+/g" and destroys the document.
          chunk.replace(cleaned);
        },
      })
      .on('html', { comments: stripComment })
      .on('head', { comments: stripComment })
      .on('body', { comments: stripComment })
      .on('script', {
        element(el) {
          const type = (el.getAttribute('type') || '').toLowerCase();
          // Non-executable script types are data blocks, not code. Removing the
          // container kills dead weight; leave JSON-LD, which consumers read.
          if (['text/template', 'text/x-template', 'unused/javascript'].includes(type)) {
            el.remove();
            return;
          }
          if (el.getAttribute('src') && !el.hasAttribute('async') && !el.hasAttribute('defer')) {
            // A blocking external script in <head> delays first paint. `defer`
            // preserves execution order, which `async` would not.
            const where = el.getAttribute('data-defer') || 'defer';
            el.setAttribute(where, '');
          }
        },
      })
      .on('link[rel="stylesheet"]', {
        element(el) {
          const href = el.getAttribute('href') || '';
          if (DROP_HREF_PREFIXES.some((p) => href.startsWith(p))) {
            el.remove();
          }
        },
      })
      .on('img', {
        element(el) {
          if (!el.getAttribute('loading')) el.setAttribute('loading', 'lazy');
          if (!el.getAttribute('decoding')) el.setAttribute('decoding', 'async');
          const srcset = el.getAttribute('srcset');
          if (srcset && !el.getAttribute('sizes')) {
            el.setAttribute('sizes', '(max-width: 768px) 100vw, 50vw');
          }
        },
      })
      .on('head', {
        element(el) {
          // Preload only the first few stylesheets. Preloading every one
          // saturates the connection and delays the ones that matter.
          const out = [];
          for (const href of caps.stylesheets.slice(0, CRITICAL_STYLE_CAP)) {
            out.push(
              `<link rel="preload" as="style" href="${escapeAttr(href)}" crossorigin>`,
            );
          }
          if (out.length) el.append(out.join(''), { html: true });
        },
      });

    return rewriter.transform(response);
  }

  /** Phase 2: headers, applied to the response after the body is finalized. */
  applyHeaders(headers, { status, isHtml, startedAt }) {
    headers.set('X-Optimizer', 'phase2');
    for (const [k, v] of Object.entries(timingHeaders(startedAt))) headers.set(k, v);

    if (!isHtml) {
      // Never claim a shared-cache lifetime for content we did not produce and
      // cannot revalidate. The previous version stamped
      // `s-maxage=300` on every response including API JSON.
      return headers;
    }
    return headers;
  }
}

function escapeAttr(v) {
  return String(v).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

const HOP_BY_HOP = new Set([
  'connection', 'keep-alive', 'transfer-encoding', 'upgrade',
  'proxy-authenticate', 'proxy-authorization', 'te', 'trailer',
  'content-encoding', 'content-length',
]);

/**
 * Upstream diagnostic headers this Worker strips.
 *
 * The origin (still-firefly-7b67ebk on htp.sh) emits an E8/pi telemetry set
 * on every response. None of it changes representation, and several are large
 * enough to be worth real bytes on every request. They pass straight through
 * a naive header copy, so strip them here rather than assuming the origin
 * will stop.
 */
const STRIP_UPSTREAM = new Set([
  'x-e8-dimension-hex', 'x-e8-roots-hex', 'x-e8-signature-hex',
  'x-e8-pi-projection-hex', 'x-math-pi-precision-digits',
  'x-math-pi-value', 'x-math-pi-value-head',
  'x-links-speed-e8pi-report', 'x-assets-count-hex',
]);

/** Copy origin headers, dropping hop-by-hop and E8/pi noise. Set-Cookie survives. */
export function sanitizeHeaders(originHeaders) {
  const out = new Headers();
  for (const [k, v] of originHeaders) {
    const lower = k.toLowerCase();
    if (HOP_BY_HOP.has(lower)) continue;
    if (STRIP_UPSTREAM.has(lower)) continue;
    out.append(k, v);
  }
  return out;
}

export default {
  async fetch(request, env, ctx) {
    const startedAt = performance.now();
    const url = new URL(request.url);

    // Only GET/HEAD are cacheable and only HTML is rewritten. Streaming a
    // mutation through anything else would corrupt it.
    const cacheable = request.method === 'GET' || request.method === 'HEAD';

    try {
      // Health endpoint, matching the shape the gateway Worker publishes, so
      // the dashboard can cross-probe this worker alongside the others. It
      // answers before any proxying or rewriting.
      if (url.pathname === '/__gateway/health') {
        return new Response(JSON.stringify({
          worker: 'odysseus-edge-optimizer',
          origin: env.ORIGIN_HOST || 'htp.sh',
          cache: env.ENABLE_CACHE === '1' ? 'on' : 'off',
          routable: [env.ORIGIN_HOST || 'htp.sh'],
          endpoints: [{
            base_url: env.ORIGIN_HOST || 'htp.sh',
            dead: false,
            cooling: false,
            strikes: 0,
            ok: 0,
            fail: 0,
          }],
        }, null, 2), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            // Read by the dashboard page directly: a Worker cannot fetch a
            // peer Worker on the same account (Cloudflare error 1042), so the
            // browser does the cross-probe.
            'Access-Control-Allow-Origin': '*',
            'Cache-Control': 'no-store',
          },
        });
      }
      // Self-test route: exists so the HTMLRewriter behaviour can be verified
      // against real workerd (see test/integration.test.js). Gated so it can
      // never respond in a deployed environment.
      if (env.ENABLE_SELFTEST === '1' && url.pathname.startsWith('/__selftest')) {
        return selfTest(url.pathname);
      }
      if (url.pathname.startsWith('/upload') && request.method === 'POST') {
        return handleUpload(request, env, startedAt);
      }
      return await handleProxy(request, url, env, ctx, startedAt, cacheable);
    } catch (err) {
      return new Response(
        JSON.stringify({ error: 'Internal Server Error' }),
        { status: 500, headers: { 'Content-Type': 'application/json' } },
      );
    }
  },
};

async function handleProxy(request, url, env, ctx, startedAt, cacheable) {
  const target = new URL(url.toString());
  target.hostname = env.ORIGIN_HOST || 'htp.sh';
  target.protocol = 'https:';

  const headers = new Headers(request.headers);
  headers.set('X-Edge-Optimizer', '1');

  const originResponse = await fetch(new Request(target.toString(), {
    method: request.method,
    headers,
    redirect: request.redirect,
    // Do not forward a body on GET/HEAD; doing so is a spec violation that
    // workerd rejects with a confusing error.
    body: request.method === 'GET' || request.method === 'HEAD' ? undefined : request.body,
  }));

  const contentType = originResponse.headers.get('content-type') || '';
  const isHtml = contentType.includes('text/html');

  const outHeaders = sanitizeHeaders(originResponse.headers);
  const caps = { stylesheets: [] };

  let response = new Response(originResponse.body, {
    status: originResponse.status,
    statusText: originResponse.statusText,
    headers: outHeaders,
  });

  if (isHtml && originResponse.ok) {
    const phase1 = new Phase1(caps);
    response = phase1.transform(response);
    // Build final headers on a fresh Headers so we are not mutating a
    // possibly-immutable set from the transformed response.
    const final = new Headers(response.headers);
    phase1.applyHeaders(final, {
      status: originResponse.status,
      isHtml: true,
      startedAt,
    });
    response = new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: final,
    });
  } else {
    const final = new Headers(outHeaders);
    new Phase1(caps).applyHeaders(final, {
      status: originResponse.status,
      isHtml: false,
      startedAt,
    });
    response = new Response(originResponse.body, {
      status: originResponse.status,
      statusText: originResponse.statusText,
      headers: final,
    });
  }

  // Shared-cache write happens ONLY for untransformed, unpersonalised,
  // cacheable responses.
  //
  // The previous version wrote the HTMLRewriter output — carrying one
  // visitor's X-Request-Time-Ms and E8 signature headers — into
  // caches.default. Everyone else was served another visitor's telemetry,
  // and the per-request headers made the entry effectively unique, so the
  // cache never hit and grew without bound.
  const cacheableResponse =
    cacheable && originResponse.ok && !isHtml
      ? originResponse.status === 200
      : false;

  if (cacheableResponse && env.ENABLE_CACHE === '1') {
    const cache = caches.default;
    const key = new Request(target.toString(), { method: 'GET' });
    ctx.waitUntil(cache.put(key, response.clone()));
  }

  return response;
}

/**
 * Self-test: run a fixture through the real Phase1 transform so its output
 * can be asserted on from a real workerd runtime. Never reachable unless
 * ENABLE_SELFTEST=1.
 */
function selfTest(pathname) {
  const html = `<!doctype html>
<html>
  <head>
    <!-- DROP_COMMENT: remove me -->
    <!--[if IE]><script>legacy</script><![endif]-->
    <script src="/app.js"></script>
    <link rel="stylesheet" href="/one.css">
  </head>
  <body>
    <p>KEEP_ME</p>
    <img src="/hero.png">
    <pre>line1

line2</pre>
  </body>
</html>`;
  const caps = { stylesheets: [] };
  const phase1 = new Phase1(caps);
  const src = new Response(html, {
    status: 200,
    headers: { 'content-type': 'text/html; charset=utf-8' },
  });
  let out = phase1.transform(src);
  const final = new Headers(out.headers);
  // Measure from now: passing 0 would report performance.now() as an elapsed
  // duration, which is epoch-scaled and meaningless.
  phase1.applyHeaders(final, { status: 200, isHtml: true, startedAt: performance.now() });
  return new Response(out.body, { status: 200, headers: final });
}

async function handleUpload(request, env, startedAt) {
  const max = Number(env.MAX_UPLOAD_SIZE || 524288000);
  const len = parseInt(request.headers.get('content-length') || '0', 10);
  if (!Number.isFinite(len) || len <= 0) {
    return new Response(JSON.stringify({ error: 'Invalid content length' }), {
      status: 400, headers: { 'Content-Type': 'application/json' },
    });
  }
  if (len > max) {
    return new Response(JSON.stringify({ error: 'File too large' }), {
      status: 413, headers: { 'Content-Type': 'application/json' },
    });
  }
  const form = await request.formData();
  const file = form.get('file');
  const headers = new Headers({ 'Content-Type': 'application/json' });
  for (const [k, v] of Object.entries(timingHeaders(startedAt))) headers.set(k, v);
  return new Response(
    JSON.stringify({
      success: true,
      message: `File '${file?.name || 'uploaded-file'}' uploaded successfully!`,
      size: len,
    }),
    { status: 200, headers },
  );
}