/**
 * Shared test setup, loaded before every test file.
 *
 * Two jobs: add the DOM matchers, and stub the browser APIs that jsdom does
 * not implement. Without the stubs, components that are perfectly fine in a
 * real browser throw here, and a test that fails for that reason teaches you
 * nothing about your code.
 */
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

// Unmount between tests so one test's DOM can never be found by the next.
afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.clearAllMocks();
});

// Recharts measures its container; jsdom reports every element as 0x0, so
// charts would silently render nothing. Give them a real size.
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
  configurable: true,
  value: 800,
});
Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
  configurable: true,
  value: 600,
});

// Used by components that respond to reduced-motion or viewport size.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});
