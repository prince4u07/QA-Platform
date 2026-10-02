/**
 * Behavioural tests for the Reports dashboard.
 *
 * These assert what a person sees and does, not how the component is built.
 * No test here inspects state, props or class names, so a refactor that keeps
 * the behaviour keeps the tests green.
 *
 * The first test pins a real bug: refreshing used to unmount the entire
 * dashboard and replace it with a full-screen spinner, which read as the page
 * reloading every time you touched anything.
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import Reports from './Reports';
import AuthProvider from '../contexts/AuthProvider';
import * as reportsApi from '../api/reports';

// The ambient background renders a three.js particle scene. It has nothing to
// do with reports and would only make these tests slow and brittle.
vi.mock('../components/AmbientBackground', () => ({ default: () => null }));
vi.mock('../components/Sidebar', () => ({ default: () => null }));
vi.mock('../api/reports');

const summary = {
  total_projects: 3,
  total_test_cases: 12,
  total_runs: 40,
  avg_health_score: 72,
  total_bugs: 9,
  open_bugs: 4,
  resolved_bugs: 5,
  avg_resolution_hours: 6,
};

/** Point every reports endpoint at canned data. */
function apiReturns(overrides = {}) {
  const data = { summary, ...overrides };
  reportsApi.getSummary.mockResolvedValue({ data: data.summary });
  reportsApi.getDetectedIssues.mockResolvedValue({ data: data.detected ?? [] });
  reportsApi.getHealthTrend.mockResolvedValue({ data: data.trend ?? [] });
  reportsApi.getBugBreakdown.mockResolvedValue({
    data: data.breakdown ?? { by_status: [], by_severity: [] },
  });
  reportsApi.getTopCategories.mockResolvedValue({ data: data.categories ?? [] });
  reportsApi.getTestPassRate.mockResolvedValue({ data: data.passRate ?? [] });
  reportsApi.getRecentRuns.mockResolvedValue({ data: data.recent ?? [] });
}

function renderReports() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <Reports />
      </AuthProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  localStorage.setItem('token', 'test-token');
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'prince', role: 'tester' }));
  apiReturns();
});

describe('Reports dashboard', () => {
  it('shows the figures once they have loaded', async () => {
    renderReports();
    expect(await screen.findByText('Reports & Analytics')).toBeInTheDocument();
    expect(await screen.findByText('40')).toBeInTheDocument();   // total runs
  });

  it('shows findings saved by the latest test runs', async () => {
    apiReturns({
      detected: [{
        id: 'run-1-seo-0',
        issue: 'Page has no meta description',
        severity: 'Moderate',
        project_name: 'Storefront',
        test_case_title: 'Homepage audit',
        location: 'https://example.test/',
      }],
    });
    renderReports();

    expect(await screen.findByText('Page has no meta description')).toBeInTheDocument();
    expect(screen.getByText(/Latest run per test case/i)).toBeInTheDocument();
  });

  it('keeps the dashboard on screen while refreshing', async () => {
    /**
     * The bug this pins: refreshing set the same `loading` flag as the first
     * load, which hit an early return and replaced the whole page with
     * "Loading reports...". Every refresh looked like a page reload and threw
     * away scroll position.
     *
     * The numbers already on screen are still true while the new ones are
     * being fetched, so they must stay visible.
     */
    const user = userEvent.setup();
    renderReports();
    await screen.findByText('40');

    // Make the refresh hang so we can observe the mid-refresh state.
    let releaseRequest;
    reportsApi.getSummary.mockReturnValue(
      new Promise((resolve) => {
        releaseRequest = () => resolve({ data: { ...summary, total_runs: 41 } });
      })
    );

    await user.click(screen.getByRole('button', { name: /refresh/i }));

    // Mid-refresh: the dashboard is still here, not a full-screen spinner.
    expect(screen.getByText('Reports & Analytics')).toBeInTheDocument();
    expect(screen.getByText('40')).toBeInTheDocument();
    expect(screen.queryByText(/loading reports/i)).not.toBeInTheDocument();

    releaseRequest();
    await waitFor(() => expect(screen.getByText('41')).toBeInTheDocument());
  });

  it('tells the user it is updating without hiding anything', async () => {
    const user = userEvent.setup();
    renderReports();
    await screen.findByText('40');

    let releaseRequest;
    reportsApi.getSummary.mockReturnValue(
      new Promise((resolve) => {
        releaseRequest = () => resolve({ data: summary });
      })
    );

    await user.click(screen.getByRole('button', { name: /refresh/i }));

    // Announced to screen readers as well as shown, since nothing else moves.
    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent(/updating/i);

    releaseRequest();
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());
  });

  it('shows an empty state instead of charts when nothing has been tested', async () => {
    apiReturns({ summary: { ...summary, total_runs: 0, total_bugs: 0 } });
    renderReports();

    expect(await screen.findByText(/no data yet/i)).toBeInTheDocument();
  });

  it('does not offer a PDF export when there is nothing to export', async () => {
    apiReturns({ summary: { ...summary, total_runs: 0, total_bugs: 0 } });
    renderReports();
    await screen.findByText(/no data yet/i);

    expect(screen.getByRole('button', { name: /export pdf/i })).toBeDisabled();
  });

  it('sends the user to login when the session has expired', async () => {
    const unauthorised = Object.assign(new Error('Unauthorized'), {
      response: { status: 401 },
    });
    reportsApi.getSummary.mockRejectedValue(unauthorised);

    renderReports();

    // The token is cleared so the app cannot keep pretending to be signed in.
    await waitFor(() => expect(localStorage.getItem('token')).toBeNull());
  });

  it('survives an endpoint returning nothing without crashing the page', async () => {
    // A backend hiccup on one panel must not take the whole dashboard down.
    reportsApi.getTopCategories.mockResolvedValue({ data: null });
    renderReports();

    expect(await screen.findByText('Reports & Analytics')).toBeInTheDocument();
  });
});
