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

// Fetch EVERY project across all pages. Dropdowns/filters need the full list,
// not just the first page (the API caps per_page at 100), otherwise a user with
// more than 100 projects silently can't select the rest.
export const getAllProjects = async () => {
  const all = [];
  let page = 1;
  let totalPages = 1;
  do {
    const res = await getProjects(page, 100);
    const body = res.data;
    const list = Array.isArray(body) ? body : (body?.data || []);
    all.push(...list);
    totalPages = body?.pagination?.pages || 1;
    page += 1;
  } while (page <= totalPages);
  return all;
};

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