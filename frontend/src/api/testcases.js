import axios, { getAuthHeader } from './axiosConfig';

const API_URL = '/testcases';

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