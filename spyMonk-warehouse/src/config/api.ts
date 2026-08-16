// Unset VITE_API_URL falls back to the local backend during `vite dev` and to
// same-origin ('') in production builds, where one process serves both the
// frontend and the API. Override in .env.local when they run on different hosts.
export const API_BASE_URL =
  import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? 'http://localhost:8000' : '');
export const API_KEY = import.meta.env.VITE_API_KEY;

export const getAuthHeaders = (): HeadersInit => {
  if (!API_KEY) {
    return {};
  }

  return {
    'X-API-Key': API_KEY,
  };
};
