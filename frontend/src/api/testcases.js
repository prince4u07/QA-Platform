import axios from 'axios';

const API_URL = 'http://127.0.0.1:5000/api/testcases';

const getAuthHeader = () => ({
  headers: {
    Authorization: `Bearer ${localStorage.getItem('token')}`,
  },
});

// List test cases (optional project filter)
export const getTestCases = (projectId) => {
  const url = projectId ? `${API_URL}?project_id=${projectId}` : API_URL;
  return axios.get(url, getAuthHeader());
};

// Create
export const createTestCase = (data) =>
  axios.post(API_URL, data, getAuthHeader());

// Update
export const updateTestCase = (id, data) =>
  axios.put(`${API_URL}/${id}`, data, getAuthHeader());

// Delete
export const deleteTestCase = (id) =>
  axios.delete(`${API_URL}/${id}`, getAuthHeader());

// Stats
export const getTestCaseStats = () =>
  axios.get(`${API_URL}/stats`, getAuthHeader());