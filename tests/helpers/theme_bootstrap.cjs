// Runs inside a browser via Playwright evaluate; parsing does not execute code.
function extractThemeBootstrap(html) {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const matches = Array.from(doc.querySelectorAll('script:not([src])'))
    .map(script => script.textContent)
    .filter(code => code.includes('Apply font early'));
  if (matches.length !== 1) {
    throw new Error(`Expected one early theme bootstrap, found ${matches.length}`);
  }
  return matches[0];
}

module.exports = { extractThemeBootstrap };
