import axios from 'axios';

const API_URL = '/ai';

const getAuthHeader = () => ({
  headers: {
    Authorization: `Bearer ${localStorage.getItem('token')}`,
  },
});

// AI calls can be slow (1-5 seconds), give them extra time
const AI_TIMEOUT = 60000; // 60 seconds

// Check if AI is configured and ready
export const checkAiHealth = () =>
  axios.get(`${API_URL}/health`, getAuthHeader());

// AI explanation for a specific bug
// Returns: { explanation, why_it_matters, how_to_fix, code_example }
export const analyzeBug = (bugId) =>
  axios.post(
    `${API_URL}/analyze-bug/${bugId}`,
    {},
    { ...getAuthHeader(), timeout: AI_TIMEOUT }
  );

// AI suggests test cases for a project
// Returns: { project_id, project_name, suggestions: [...] }
export const suggestTestCases = (projectId) =>
  axios.post(
    `${API_URL}/suggest-tests/${projectId}`,
    {},
    { ...getAuthHeader(), timeout: AI_TIMEOUT }
  );

// AI suggests fix for a specific test finding
// Pass: { category, item, context? }
// Returns: { what_it_is, user_impact, how_to_fix, code_example }
export const suggestFix = (finding) =>
  axios.post(
    `${API_URL}/suggest-fix`,
    finding,
    { ...getAuthHeader(), timeout: AI_TIMEOUT }
  );

// Chat with AI assistant
// Pass: { message, history?, include_context? }
// Returns: { reply }
export const chatWithAi = (message, history = [], includeContext = false) =>
  axios.post(
    `${API_URL}/chat`,
    { message, history, include_context: includeContext },
    { ...getAuthHeader(), timeout: AI_TIMEOUT }
  );