import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import Dashboard from './Dashboard';
import Projects from './Projects';
import TestCases from './TestCases';
import Reports from './Reports';
import Admin from './Admin';
import AuthProvider from '../contexts/AuthProvider';
import { getAuthHeader } from '../api/axiosConfig';

vi.mock('../components/AmbientBackground', () => ({ default: () => null }));
vi.mock('../components/Sidebar', () => ({ default: () => null }));
vi.mock('../api/reports');
vi.mock('../api/projects', () => ({
  getProjects: vi.fn().mockResolvedValue({ data: { data: [], pagination: { pages: 0, total: 0 } } }),
}));

function renderPage(ui) {
  return render(
    <MemoryRouter>
      <AuthProvider>{ui}</AuthProvider>
    </MemoryRouter>
  );
}

function renderWithRoutes(ui) {
  return render(
    <MemoryRouter initialEntries={['/projects']}>
      <AuthProvider>
        <Routes>
          <Route path="/projects" element={ui} />
          <Route path="/login" element={<h1>Sign in</h1>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  localStorage.clear();
});

describe('a signed-out visitor can browse the app', () => {
  it('shows the dashboard instead of bouncing to login', () => {
    renderPage(<Dashboard />);
    expect(screen.getByText(/Welcome to/i)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Sign in' })).not.toBeInTheDocument();
  });

  it('asks the dashboard visitor to sign in before using the features', () => {
    renderPage(<Dashboard />);
    expect(screen.getByText(/browsing as a guest/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /sign in/i })).toBeInTheDocument();
  });

  it('opens the projects page without a session', () => {
    renderPage(<Projects />);
    expect(screen.getByText(/projects are private/i)).toBeInTheDocument();
  });

  it('opens the reports page without a session', () => {
    renderPage(<Reports />);
    expect(screen.getByText(/generated from your runs/i)).toBeInTheDocument();
  });

  it('explains the admin console instead of showing an empty table', () => {
    renderPage(<Admin />);
    expect(screen.getByText(/manages every account/i)).toBeInTheDocument();
  });

  it('can still see the test case page and its create button', () => {
    renderPage(<TestCases />);
    // The button must not be dead: a guest clicking it should be taken to
    // login rather than finding it disabled.
    expect(screen.getByRole('button', { name: /new test case/i })).toBeEnabled();
  });
});

describe('feature actions require a session', () => {
  it('sends a guest from New project to the login page', async () => {
    const user = userEvent.setup();
    renderWithRoutes(<Projects />);
    await user.click(screen.getByRole('button', { name: /new project/i }));
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  });

  it('sends a guest from Export PDF to the login page', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/reports']}>
        <AuthProvider>
          <Routes>
            <Route path="/reports" element={<Reports />} />
            <Route path="/login" element={<h1>Sign in</h1>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );
    await user.click(screen.getByRole('button', { name: /export pdf/i }));
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  });
});

describe('signed-out requests are not malformed', () => {
  it('sends no Authorization header instead of "Bearer null"', () => {
    // "Bearer null" made Flask reject guest requests as 422 malformed
    // rather than the 401 that actually describes the situation.
    expect(getAuthHeader()).toEqual({});
  });

  it('sends the real token once signed in', () => {
    localStorage.setItem('token', 'abc123');
    expect(getAuthHeader()).toEqual({ headers: { Authorization: 'Bearer abc123' } });
  });
});

describe('a signed-in visitor', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token');
    localStorage.setItem('user', JSON.stringify({ id: 1, username: 'prince', role: 'tester' }));
  });

  it('is not asked to sign in again', () => {
    renderPage(<Projects />);
    expect(screen.queryByText(/projects are private/i)).not.toBeInTheDocument();
  });
});
