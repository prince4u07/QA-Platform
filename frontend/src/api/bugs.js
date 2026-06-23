import axios from 'axios';

const API_URL = '/bugs';

const getAuthHeader = () => ({
  headers: {
    Authorization: `Bearer ${localStorage.getItem('token')}`,
  },
});

// List bugs (with optional filters)
// filters: { status, severity, category, project_id }
export const getBugs = (filters = {}) => {
  const params = new URLSearchParams();
  if (filters.status) params.append('status', filters.status);
  if (filters.severity) params.append('severity', filters.severity);
  if (filters.category) params.append('category', filters.category);
  if (filters.project_id) params.append('project_id', filters.project_id);
  const qs = params.toString();
  return axios.get(qs ? `${API_URL}?${qs}` : API_URL, getAuthHeader());
};

// Get single bug
export const getBug = (id) =>
  axios.get(`${API_URL}/${id}`, getAuthHeader());

// Create a new bug
export const createBug = (data) =>
  axios.post(API_URL, data, getAuthHeader());

// Update bug
export const updateBug = (id, data) =>
  axios.put(`${API_URL}/${id}`, data, getAuthHeader());

// Quick status change (Open / In Progress / Resolved / Closed)
export const updateBugStatus = (id, status) =>
  axios.patch(`${API_URL}/${id}/status`, { status }, getAuthHeader());

// Delete bug
export const deleteBug = (id) =>
  axios.delete(`${API_URL}/${id}`, getAuthHeader());

// Get bug stats (counts by status)
export const getBugStats = () =>
  axios.get(`${API_URL}/stats`, getAuthHeader());

// 🪄 The magic: auto-create bugs from a test run's findings
export const createBugsFromTestRun = (testRunId) =>
  axios.post(`${API_URL}/from-test-run/${testRunId}`, {}, getAuthHeader());