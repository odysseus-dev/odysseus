// Shared search-provider marks used by Settings and Deep Research.
export const SEARCH_PROVIDER_LOGOS = {
  searxng: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M10 4a6 6 0 1 0 0 12 6 6 0 0 0 0-12zm0-2a8 8 0 1 1-4.93 14.32l-3.4 3.4a1 1 0 1 1-1.4-1.4l3.4-3.4A8 8 0 0 1 10 2zM13 8.5L11.5 10 13 11.5l-1 1L10.5 11 9 12.5l-1-1L9.5 10 8 8.5l1-1L10.5 9 12 7.5z"/></svg>',
  duckduckgo: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-1.5 5.5a1.2 1.2 0 1 1 0 2.4 1.2 1.2 0 0 1 0-2.4zm5 0a1.2 1.2 0 1 1 0 2.4 1.2 1.2 0 0 1 0-2.4zM12 13c-1.5 0-3.6.8-3.6 2.5C8.4 17.2 10.4 18 12 18s3.6-.8 3.6-2.5C15.6 13.8 13.5 13 12 13z"/></svg>',
  brave: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M19 4l-1.5 1L15 3l-3 .5L9 3 6.5 5 5 4 3 7l1.5 2L4 12l3 5 4 3 1 1 1-1 4-3 3-5-.5-3L21 7l-2-3zM12 17l-2.5-2 .5-3-2-1.5 2-1.5L11 7l3-1 3 1-.5 2 2 1.5-2 1.5.5 3L14.5 17 12 17z"/></svg>',
  google_pse: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M21.35 11.1H12v3.2h5.35c-.5 2.4-2.55 4-5.35 4-3.25 0-5.9-2.65-5.9-5.9s2.65-5.9 5.9-5.9c1.55 0 2.95.55 4.05 1.55l2.4-2.4C16.85 4.05 14.55 3 12 3 7 3 3 7 3 12s4 9 9 9c5.2 0 8.65-3.65 8.65-8.8 0-.4-.05-.7-.3-1.1z"/></svg>',
  tavily: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2L2 8.5l4 2.5v6l6 3.5 6-3.5v-6l4-2.5L12 2zm-4 9.5L12 14l4-2.5V16l-4 2.5L8 16v-4.5z"/></svg>',
  serper: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M11 4a7 7 0 1 0 4.2 12.6l4.5 4.5 1.4-1.4-4.5-4.5A7 7 0 0 0 11 4zm0 2a5 5 0 1 1 0 10 5 5 0 0 1 0-10zm-1 2v2H8v2h2v2h2v-2h2V10h-2V8h-2z"/></svg>',
  disabled: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
};

export function searchProviderLogo(provider) {
  const key = provider === 'google' ? 'google_pse' : provider;
  return SEARCH_PROVIDER_LOGOS[key] || '';
}
