import axios from 'axios';
import { API_BASE } from '../config';

const API_URL = `${API_BASE}/runner`;

const getAuthHeader = () => ({
  headers: {
    Authorization: `Bearer ${localStorage.getItem('token')}`,
  },
});

// Trigger a test run for an automated test case (synchronous, blocks until done)
export const runTestCase = (testCaseId) =>
  axios.post(`${API_URL}/run/${testCaseId}`, {}, {
    ...getAuthHeader(),
    timeout: 900000,  // 15 minutes for multi-page tests
  });

// Queue a test run on a background worker; returns { job_id } immediately.
// Pass force=true to run even when no login session is captured (after the user
// has confirmed they want a logged-out crawl).
export const runTestCaseAsync = (testCaseId, force = false) =>
  axios.post(`${API_URL}/run/${testCaseId}/async${force ? '?force=true' : ''}`,
    {}, getAuthHeader());

// Poll a queued run's status/progress/result.
export const getRunJob = (jobId) =>
  axios.get(`${API_URL}/job/${jobId}`, getAuthHeader());

// Ask the worker to stop the crawl gracefully (saves whatever's captured so far).
export const cancelRunJob = (jobId) =>
  axios.post(`${API_URL}/job/${jobId}/cancel`, {}, getAuthHeader());

// ---- Manual (tracked) test run ----
// Opens a headed browser at the project URL; captures a screenshot on every
// page navigation so the user's manual walkthrough leaves auditable evidence.
export const startManualRun = (testCaseId) =>
  axios.post(`${API_URL}/manual/${testCaseId}/start`, {}, getAuthHeader());

export const getManualRunStatus = (testCaseId) =>
  axios.get(`${API_URL}/manual/${testCaseId}/status`, getAuthHeader());

export const finishManualRun = (testCaseId, outcome, note = '') =>
  axios.post(`${API_URL}/manual/${testCaseId}/done`,
    { outcome, note }, getAuthHeader());

// Tell the worker to BFS-crawl from the page the user has logged in to.
export const autoCrawlManualRun = (testCaseId) =>
  axios.post(`${API_URL}/manual/${testCaseId}/autocrawl`, {}, getAuthHeader());

export const cancelManualRun = (testCaseId) =>
  axios.post(`${API_URL}/manual/${testCaseId}/cancel`, {}, getAuthHeader());

// Get test run history for a test case
export const getTestRuns = (testCaseId) =>
  axios.get(`${API_URL}/runs/${testCaseId}`, getAuthHeader());

// NEW IN CHUNK 3: Get per-page results for a multi-page test run
export const getCrawledPages = (runId) =>
  axios.get(`${API_URL}/run/${runId}/pages`, getAuthHeader());