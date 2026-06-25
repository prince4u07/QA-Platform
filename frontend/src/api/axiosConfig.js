import axios from 'axios';
import { API_BASE } from '../config';

// Set default base URL for all API calls (env-overridable via VITE_API_URL).
axios.defaults.baseURL = API_BASE;

// Global axios setup: intercepts all responses
// If any API call returns 401 (token expired/invalid), 
// clear storage and redirect to login automatically.

axios.interceptors.response.use(
  // Success — just pass through
  (response) => response,

  // Error — check for auth failures
  (error) => {
    console.error('API Error:', error.config?.url, error.response?.status);
    
    // Token expired or invalid
    if (error.response?.status === 401) {
      // Clear any auth data
      localStorage.removeItem('token');
      localStorage.removeItem('user');

      // Don't redirect if already on login/register pages
      const currentPath = window.location.pathname;
      if (currentPath !== '/login' && currentPath !== '/register') {
        // Save where they were trying to go (optional UX nicety)
        sessionStorage.setItem('redirectAfterLogin', currentPath);

        // Show a brief message before redirect
        alert('Your session has expired. Please log in again.');

        // Redirect to login
        window.location.href = '/login';
      }
    }

    // Pass the error along so components can still handle other errors
    return Promise.reject(error);
  }
);

export default axios;