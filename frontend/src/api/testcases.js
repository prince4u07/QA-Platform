import axios from 'axios';
import { API_BASE } from '../config';

const API_URL = `${API_BASE}/testcases`;

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

// Quick status change
export const updateTestCaseStatus = (id, status) =>
  axios.patch(`${API_URL}/${id}/status`, { status }, getAuthHeader());

// Delete
export const deleteTestCase = (id) =>
  axios.delete(`${API_URL}/${id}`, getAuthHeader());

// Stats
export const getTestCaseStats = () =>
  axios.get(`${API_URL}/stats`, getAuthHeader());