/**
 * Central HTTP client — axios with configurable base path and interceptors.
 * Axios is loaded globally from CDN (see index.html).
 */

function normalizeBasePath(raw) {
  if (!raw || raw === '/') return '';
  return String(raw).replace(/\/+$/, '');
}

export function getBasePath() {
  if (typeof window === 'undefined') return '';
  return normalizeBasePath(window.__ODYSSEUS_BASE_PATH ?? '');
}

/** Build a same-origin API URL (window.open, img src, streaming fetch). */
export function apiPath(path) {
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${getBasePath()}${p}`;
}

export function apiErrorMessage(err) {
  if (!err) return 'Request failed';
  const data = err.response?.data;
  if (data?.error?.message) return data.error.message;
  if (typeof data?.error === 'string') return data.error;
  if (typeof data?.detail === 'string') return data.detail;
  if (err.message) return err.message;
  return 'Connection failed. Please check your internet and try again.';
}

function createApiClient() {
  const axios = window.axios;
  if (!axios) {
    throw new Error('axios is not loaded — include axios.min.js before app modules');
  }

  const instance = axios.create({
    baseURL: getBasePath() || undefined,
    withCredentials: true,
  });

  instance.interceptors.response.use(
    (response) => response,
    (error) => {
      const status = error.response?.status;
      const url = error.config?.url || '';
      if (status === 401 && !String(url).includes('/api/auth/')) {
        window.location.href = apiPath('/login');
      }
      console.error('[api]', apiErrorMessage(error));
      return Promise.reject(error);
    },
  );

  return instance;
}

const _client = createApiClient();

export const api = {
  get: (...args) => _client.get(...args),
  post: (...args) => _client.post(...args),
  put: (...args) => _client.put(...args),
  patch: (...args) => _client.patch(...args),
  delete: (...args) => _client.delete(...args),
  request: (...args) => _client.request(...args),
};

/** fetch() for SSE/streaming endpoints that need ReadableStream in the browser. */
export async function apiFetch(path, options = {}) {
  const url = path.startsWith('http://') || path.startsWith('https://') ? path : apiPath(path);
  const res = await fetch(url, { credentials: 'same-origin', ...options });
  if (res.status === 401 && !url.includes('/api/auth/')) {
    window.location.href = apiPath('/login');
  }
  return res;
}
