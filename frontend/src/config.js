// Central host configuration.
//
// All API calls and backend asset URLs (screenshots, evidence crops, PDF
// exports) resolve against this. Override per environment with a Vite env var
// (e.g. `VITE_API_URL=https://api.example.com` in a .env file or the build
// environment); it falls back to the local dev backend.
export const SERVER_BASE =
  import.meta.env.VITE_API_URL || 'http://127.0.0.1:5000';

// Root of the JSON API.
export const API_BASE = `${SERVER_BASE}/api`;

// Build an absolute URL for a backend-served asset path (e.g. "/static/...").
export const assetUrl = (path) => `${SERVER_BASE}${path || ''}`;
