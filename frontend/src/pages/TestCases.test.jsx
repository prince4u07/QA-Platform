import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import TestCases from './TestCases';
import AuthProvider from '../contexts/AuthProvider';
import * as projectsApi from '../api/projects';
import * as testcasesApi from '../api/testcases';

vi.mock('../api/projects', () => ({ getProjects: vi.fn() }));
vi.mock('../api/testcases', () => ({
  getTestCases: vi.fn(),
  createTestCase: vi.fn(),
  updateTestCase: vi.fn(),
  deleteTestCase: vi.fn(),
}));
vi.mock('../api/runner', () => ({
  runTestCaseAsync: vi.fn(), getRunJob: vi.fn(), cancelRunJob: vi.fn(),
  getTestRuns: vi.fn(), getCrawledPages: vi.fn(), startManualRun: vi.fn(),
  getManualRunStatus: vi.fn(), finishManualRun: vi.fn(), cancelManualRun: vi.fn(),
  markManualStep: vi.fn(), reportManualIssue: vi.fn(),
}));
vi.mock('../api/bugs', () => ({ createBugsFromTestRun: vi.fn() }));
vi.mock('../components/Sidebar', () => ({ default: () => null }));
vi.mock('../components/AmbientBackground', () => ({ default: () => null }));

const manualTest = {
  id: 4,
  project_id: 1,
  project_name: 'Storefront',
  title: 'Explore checkout',
  description: '',
  steps: 'Open checkout',
  expected_result: 'Checkout stays usable',
  priority: 'Medium',
  status: 'Pending',
  test_type: 'manual',
  max_pages: 10,
};

function renderPage(testCases = [manualTest]) {
  projectsApi.getProjects.mockResolvedValue({
    data: { data: [{ id: 1, name: 'Storefront' }] },
  });
  testcasesApi.getTestCases.mockResolvedValue({ data: testCases });
  testcasesApi.createTestCase.mockResolvedValue({ data: { id: 5 } });
  // A signed-in session: the app treats a visitor as authenticated only when
  // both the token and the user record are present.
  localStorage.setItem('token', 'test-token');
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'prince', role: 'tester' }));

  return render(
    <MemoryRouter>
      <AuthProvider>
        <TestCases />
      </AuthProvider>
    </MemoryRouter>
  );
}

async function fillRequiredFields(user) {
  await user.selectOptions(screen.getByRole('combobox', { name: 'Project *' }), '1');
  await user.type(screen.getByRole('textbox', { name: /^title/i }), 'Checkout works');
  await user.type(screen.getByRole('textbox', { name: /^steps/i }), 'Open /checkout');
  await user.type(screen.getByRole('textbox', { name: /expected result/i }), 'Checkout is shown');
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('Test case workflow', () => {
  it('explains both modes and keeps run history available for manual tests', async () => {
    renderPage();

    expect(await screen.findByText('Explore checkout')).toBeInTheDocument();
    expect(screen.getByText(/exploratory and usability checks/i)).toBeInTheDocument();
    expect(screen.getByText(/repeatable regression and performance checks/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /history/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^pass$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^fail$/i })).not.toBeInTheDocument();
  });

  it('creates a manual test with a pending run result and no status field', async () => {
    const user = userEvent.setup();
    renderPage([]);
    await screen.findByText(/no test cases yet/i);

    await user.click(screen.getByRole('button', { name: /new test case/i }));
    await fillRequiredFields(user);
    await user.click(screen.getByRole('button', { name: /create test case/i }));

    await waitFor(() => expect(testcasesApi.createTestCase).toHaveBeenCalled());
    expect(testcasesApi.createTestCase).toHaveBeenCalledWith(expect.objectContaining({
      test_type: 'manual',
    }));
    const payload = testcasesApi.createTestCase.mock.calls[0][0];
    expect(payload).not.toHaveProperty('status');
    // The purpose field was removed: it was stored but never read by the
    // runner, the reports, or the UI, so it must not be sent either.
    expect(payload).not.toHaveProperty('test_purpose');
    // The framework field was removed too: the only runner is Playwright, so
    // there is nothing to choose or store.
    expect(payload).not.toHaveProperty('automation_framework');
  });

  it('crawls the whole site when automated and never sends a framework claim', async () => {
    const user = userEvent.setup();
    renderPage([]);
    await screen.findByText(/no test cases yet/i);

    await user.click(screen.getByRole('button', { name: /new test case/i }));
    await fillRequiredFields(user);
    await user.click(screen.getByRole('radio', { name: /automated/i }));

    // The dead-control check only applies to automated runs, so its toggle
    // appears with the mode rather than being shown and ignored for manual.
    expect(screen.getByRole('checkbox', { name: /click controls/i })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /create test case/i }));

    await waitFor(() => expect(testcasesApi.createTestCase).toHaveBeenCalled());
    expect(testcasesApi.createTestCase).toHaveBeenCalledWith(expect.objectContaining({
      test_type: 'automated',
      crawl_pages: true,
    }));
    expect(testcasesApi.createTestCase.mock.calls[0][0]).not.toHaveProperty('automation_framework');
  });

  it('hides the dead-control toggle for manual tests, where it does nothing', async () => {
    const user = userEvent.setup();
    renderPage([]);
    await screen.findByText(/no test cases yet/i);

    await user.click(screen.getByRole('button', { name: /new test case/i }));
    await fillRequiredFields(user);
    expect(screen.queryByRole('checkbox', { name: /click controls/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole('radio', { name: /automated/i }));
    expect(screen.getByRole('checkbox', { name: /click controls/i })).toBeInTheDocument();
  });

  it('leaves control clicking off unless it is switched on', async () => {
    const user = userEvent.setup();
    renderPage([]);
    await screen.findByText(/no test cases yet/i);

    await user.click(screen.getByRole('button', { name: /new test case/i }));
    await fillRequiredFields(user);
    await user.click(screen.getByRole('radio', { name: /automated/i }));
    await user.click(screen.getByRole('button', { name: /create test case/i }));

    await waitFor(() => expect(testcasesApi.createTestCase).toHaveBeenCalled());
    expect(testcasesApi.createTestCase).toHaveBeenCalledWith(expect.objectContaining({
      check_dead_controls: false,
    }));
  });

  it('says what clicking controls will do before you switch it on', async () => {
    const user = userEvent.setup();
    renderPage([]);
    await screen.findByText(/no test cases yet/i);

    await user.click(screen.getByRole('button', { name: /new test case/i }));

    await user.click(screen.getByRole('radio', { name: /automated/i }));
    const optIn = screen.getByRole('checkbox', { name: /click controls/i });
    expect(optIn).not.toBeChecked();
    expect(screen.getByText(/skips anything inside a form/i)).toBeInTheDocument();

    await fillRequiredFields(user);
    await user.click(optIn);
    await user.click(screen.getByRole('button', { name: /create test case/i }));

    await waitFor(() => expect(testcasesApi.createTestCase).toHaveBeenCalled());
    expect(testcasesApi.createTestCase).toHaveBeenCalledWith(expect.objectContaining({
      check_dead_controls: true,
    }));
  });
});

