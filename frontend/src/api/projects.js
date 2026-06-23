import axios from 'axios';

const API_URL = '/projects';

const getAuthHeader = () => ({
  headers: {
    Authorization: `Bearer ${localStorage.getItem('token')}`,
  },
});

// List all projects (with pagination)
export const getProjects = (page = 1, perPage = 10) => 
  axios.get(`${API_URL}?page=${page}&per_page=${perPage}`, getAuthHeader());

// Get a single project
export const getProject = (id) =>
  axios.get(`${API_URL}/${id}`, getAuthHeader());

// Create URL-based project
export const createProject = (data) =>
  axios.post(API_URL, data, getAuthHeader());

// Update project
export const updateProject = (id, data) =>
  axios.put(`${API_URL}/${id}`, data, getAuthHeader());

// Delete project
export const deleteProject = (id) =>
  axios.delete(`${API_URL}/${id}`, getAuthHeader());

// Upload ZIP-based project
export const uploadProject = (formData, onProgress) => {
  return axios.post(`${API_URL}/upload`, formData, {
    headers: {
      Authorization: `Bearer ${localStorage.getItem('token')}`,
      'Content-Type': 'multipart/form-data',
    },
    onUploadProgress: (progressEvent) => {
      if (onProgress && progressEvent.total) {
        const percent = Math.round((progressEvent.loaded * 100) / progressEvent.total);
        onProgress(percent);
      }
    },
  });
};

// Dashboard stats
export const getDashboardStats = () =>
  axios.get(`${API_URL}/stats`, getAuthHeader());

// ----- Manual login + session capture (Chunk D-2) -----

// Open a headed Chromium window for manual login
export const startLogin = (id) =>
  axios.post(`${API_URL}/${id}/start-login`, {}, getAuthHeader());

// User finished logging in -> save the session cookies
export const saveSession = (id) =>
  axios.post(`${API_URL}/${id}/save-session`, {}, getAuthHeader());

// Close the login window without saving
export const cancelLogin = (id) =>
  axios.post(`${API_URL}/${id}/cancel-login`, {}, getAuthHeader());

// Poll the state of an in-progress login window
export const getLoginStatus = (id) =>
  axios.get(`${API_URL}/${id}/login-status`, getAuthHeader());