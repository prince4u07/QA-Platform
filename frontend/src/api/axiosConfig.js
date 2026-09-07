import axios from 'axios';

// Base URL for the API. Overridable via VITE_API_URL for non-local deploys.
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:5000/api';
axios.defaults.baseURL = API_BASE_URL;

// Server origin (no /api suffix) for resolving screenshot / evidence paths.
export const apiOrigin = () => API_BASE_URL.replace(/\/api\/?$/, '');

// Join a backend static path (e.g. "/static/uploads/...") onto the server origin.
export const imgUrl = (path) => {
  if (!path) return '';
  if (/^https?:\/\//i.test(path)) return path;
  return `${apiOrigin()}${path.startsWith('/') ? path : `/${path}`}`;
};

// Single shared auth header builder (replaces 6+ copies across api/ modules).
// Prefer the request interceptor below; this is kept for explicit calls.
export const getAuthHeader = () => ({
  headers: {
    Authorization: `Bearer ${localStorage.getItem('token')}`,
  },
});

// Attach the token to every request automatically so callers don't have to.
axios.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token && !config.headers?.Authorization) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Global response handling: on 401 clear auth and redirect to login.
// Emits a 'qa:session-expired' event instead of window.alert() so the UI
// can show a non-blocking toast/message.
axios.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token');
      localStorage.removeItem('user');

      const currentPath = window.location.pathname;
      if (currentPath !== '/login' && currentPath !== '/register') {
        sessionStorage.setItem('redirectAfterLogin', currentPath);
        window.dispatchEvent(new CustomEvent('qa:session-expired'));
        window.location.href = '/login';
      }
    }
    return Promise.reject(error);
  }
);

export default axios;
