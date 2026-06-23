import { Component } from 'react';

/**
 * Catches any error thrown while rendering the lazy 3D scene (WebGL context
 * failures, runtime errors, chunk-load errors) and renders `fallback` instead,
 * so a broken background can never blank out the whole page.
 */
export default class SceneErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error) {
    // Non-fatal: log once so it's visible in the console, then show the fallback.
    console.warn('[3D background] disabled after error:', error);
  }

  render() {
    if (this.state.failed) {
      return this.props.fallback ?? null;
    }
    return this.props.children;
  }
}
