import axios, { getAuthHeader } from './axiosConfig';

const API_URL = '/runner';

// Queue a test run on a background worker; returns { job_id } immediately.
export const runTestCaseAsync = (testCaseId) =>
  axios.post(`${API_URL}/run/${testCaseId}/async`, {}, getAuthHeader());

// Poll a queued run's status/progress/result.
export const getRunJob = (jobId) =>
  axios.get(`${API_URL}/job/${jobId}`, getAuthHeader());

// Ask the worker to stop the crawl gracefully (saves whatever's captured so far).
export const cancelRunJob = (jobId) =>
  axios.post(`${API_URL}/job/${jobId}/cancel`, {}, getAuthHeader());

// Retry a failed automated run as a fresh job.
export const retryTestCase = (testCaseId) =>
  axios.post(`${API_URL}/run/${testCaseId}/retry`, {}, getAuthHeader());

// Re-run the automated test that produced an issue.
export const rerunIssueTest = (testCaseId) =>
  axios.post(`${API_URL}/run/${testCaseId}/async`, {}, getAuthHeader());

// ---- Manual (tracked) test run ----
// Opens a headed browser at the project URL; captures a screenshot on every
// page navigation so the user's manual walkthrough leaves auditable evidence.
export const startManualRun = (testCaseId) =>
  axios.post(`${API_URL}/manual/${testCaseId}/start`, {}, getAuthHeader());

export const getManualRunStatus = (testCaseId) =>
  axios.get(`${API_URL}/manual/${testCaseId}/status`, getAuthHeader());

// expectedMet answers "did what you saw match what the business asked for",
// which is the verdict a manual run actually exists to produce.
export const finishManualRun = (testCaseId, outcome, note = '', expectedMet = null) =>
  axios.post(`${API_URL}/manual/${testCaseId}/done`,
    { outcome, note, expected_met: expectedMet }, getAuthHeader());

// Tell the worker to BFS-crawl from the page the user has logged in to.
export const autoCrawlManualRun = (testCaseId) =>
  axios.post(`${API_URL}/manual/${testCaseId}/autocrawl`, {}, getAuthHeader());

// Tick one checklist step off while testing by hand.
export const markManualStep = (testCaseId, index, status, note = '', issueRef = null) =>
  axios.post(`${API_URL}/manual/${testCaseId}/step/${index}`,
    { status, note, issue_ref: issueRef }, getAuthHeader());

// Pause / resume the live manual session without closing the browser.
export const pauseManualRun = (testCaseId, paused = true) =>
  axios.post(`${API_URL}/manual/${testCaseId}/pause`, { paused }, getAuthHeader());

// Report something spotted by eye, against the page currently on screen.
export const reportManualIssue = (testCaseId, issue) =>
  axios.post(`${API_URL}/manual/${testCaseId}/issue`, issue, getAuthHeader());

export const cancelManualRun = (testCaseId) =>
  axios.post(`${API_URL}/manual/${testCaseId}/cancel`, {}, getAuthHeader());

// Get test run history for a test case
export const getTestRuns = (testCaseId) =>
  axios.get(`${API_URL}/runs/${testCaseId}`, getAuthHeader());

// NEW IN CHUNK 3: Get per-page results for a multi-page test run
export const getCrawledPages = (runId) =>
  axios.get(`${API_URL}/run/${runId}/pages`, getAuthHeader());