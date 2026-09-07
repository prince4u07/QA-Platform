import axios, { getAuthHeader } from './axiosConfig';

const API_URL = '/reports';

// Top-level KPI cards
export const getSummary = () =>
  axios.get(`${API_URL}/summary`, getAuthHeader());

// Findings from the latest run for each test case
export const getDetectedIssues = () =>
  axios.get(`${API_URL}/detected-issues`, getAuthHeader());

// Health score over time (line chart)
export const getHealthTrend = () =>
  axios.get(`${API_URL}/health-trend`, getAuthHeader());

// Bug status + severity distribution (pie charts)
export const getBugBreakdown = () =>
  axios.get(`${API_URL}/bug-breakdown`, getAuthHeader());

// Top bug categories (horizontal bar chart)
export const getTopCategories = () =>
  axios.get(`${API_URL}/top-categories`, getAuthHeader());

// Test pass/fail rate over time (stacked bar chart)
export const getTestPassRate = () =>
  axios.get(`${API_URL}/test-pass-rate`, getAuthHeader());

// Last 10 test runs
export const getRecentRuns = () =>
  axios.get(`${API_URL}/recent-runs`, getAuthHeader());