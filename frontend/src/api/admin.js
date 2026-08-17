import axios from 'axios';

const API_URL = '/admin';

const getAuthHeader = () => ({
  headers: {
    Authorization: `Bearer ${localStorage.getItem('token')}`,
  },
});

/**
 * Is the signed-in account an administrator?
 *
 * Used only to decide what to show. The server checks the role on every
 * admin request against the database, so hiding the link is a convenience,
 * never the security boundary.
 */
export const isAdmin = () => {
  try {
    return JSON.parse(localStorage.getItem('user') || '{}').role === 'admin';
  } catch {
    return false;
  }
};

// Platform-wide totals and recent activity across every account.
export const getOverview = () =>
  axios.get(`${API_URL}/overview`, getAuthHeader());

// Every account, with a count of what it owns.
export const getUsers = () =>
  axios.get(`${API_URL}/users`, getAuthHeader());

// One account's projects and recent runs.
export const getUserDetail = (userId) =>
  axios.get(`${API_URL}/users/${userId}`, getAuthHeader());

// Change a role, or activate/deactivate an account.
export const updateUser = (userId, changes) =>
  axios.patch(`${API_URL}/users/${userId}`, changes, getAuthHeader());

// Irreversible. The server requires the exact username as confirmation.
export const deleteUser = (userId, confirmUsername) =>
  axios.delete(`${API_URL}/users/${userId}`, {
    ...getAuthHeader(),
    data: { confirm_username: confirmUsername },
  });
