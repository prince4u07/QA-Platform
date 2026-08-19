import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import TestCases from './TestCases';
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
  test_purpose: 'exploratory',
  max_pages: 10,
};

function renderPage(testCases = [manualTest]) {
  projectsApi.getProjects.mockResolvedValue({
    data: { data: [{ id: 1, name: 'Storefront' }] },
  });
  testcasesApi.getTestCases.mockResolvedValue({ data: testCases });
  testcasesApi.createTestCase.mockResolvedValue({ data: { id: 5 } });
  localStorage.setItem('token', 'test-token');

  return render(
    <MemoryRouter>
      <TestCases />
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

  it('creates a manual usability test with a pending run result', async () => {
    const user = userEvent.setup();
    renderPage([]);
    await screen.findByText(/no test cases yet/i);

    await user.click(screen.getByRole('button', { name: /new test case/i }));
    await fillRequiredFields(user);
    await user.selectOptions(screen.getByRole('combobox', { name: /test purpose/i }), 'usability');
    await user.click(screen.getByRole('button', { name: /create test case/i }));

    await waitFor(() => expect(testcasesApi.createTestCase).toHaveBeenCalled());
    expect(testcasesApi.createTestCase).toHaveBeenCalledWith(expect.objectContaining({
      test_type: 'manual',
      test_purpose: 'usability',
      automation_framework: 'none',
    }));
    expect(testcasesApi.createTestCase.mock.calls[0][0]).not.toHaveProperty('status');
  });

  it('offers automated purposes and records Playwright as the runner', async () => {
    const user = userEvent.setup();
    renderPage([]);
    await screen.findByText(/no test cases yet/i);

    await user.click(screen.getByRole('button', { name: /new test case/i }));
    await fillRequiredFields(user);
    await user.click(screen.getByRole('radio', { name: /automated/i }));

    const purpose = screen.getByRole('combobox', { name: /test purpose/i });
    expect(within(purpose).getByRole('option', { name: /regression/i })).toBeInTheDocument();
    expect(within(purpose).getByRole('option', { name: /performance/i })).toBeInTheDocument();
    expect(within(purpose).queryByRole('option', { name: /usability/i })).not.toBeInTheDocument();

    await user.selectOptions(purpose, 'regression');
    await user.click(screen.getByRole('button', { name: /create test case/i }));

    await waitFor(() => expect(testcasesApi.createTestCase).toHaveBeenCalled());
    expect(testcasesApi.createTestCase).toHaveBeenCalledWith(expect.objectContaining({
      test_type: 'automated',
      test_purpose: 'regression',
      automation_framework: 'playwright',
      crawl_pages: true,
    }));
  });
});
