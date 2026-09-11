import { Suspense, lazy, useState } from 'react';
import SceneErrorBoundary from './SceneErrorBoundary';

// Reuses the same code-split three.js chunk as the auth pages.
const ParticleScene = lazy(() => import('./three/ParticleScene'));

function supportsWebGL() {
  try {
    const canvas = document.createElement('canvas');
    return !!(
      window.WebGLRenderingContext &&
      (canvas.getContext('webgl') || canvas.getContext('experimental-webgl'))
    );
  } catch {
    return false;
  }
}

function detectCapable() {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const small = window.matchMedia('(max-width: 767px)').matches;
  return !reduce && !small && supportsWebGL();
}

/**
 * Subtle, app-wide animated backdrop for in-app pages.
 * Fixed + pointer-events-none so it never interferes with clicks/scroll,
 * dimmed heavily so foreground content keeps AA contrast.
 */
export default function AmbientBackground() {
  const [enable3D] = useState(detectCapable);

  return (
    <div className="fixed inset-0 -z-10 overflow-hidden bg-base pointer-events-none">
      <div className="absolute inset-0 bg-brand-radial animate-mesh-drift opacity-70" />
      {enable3D && (
        <div className="absolute inset-0 opacity-40">
          <SceneErrorBoundary>
            <Suspense fallback={null}>
              <ParticleScene />
            </Suspense>
          </SceneErrorBoundary>
        </div>
      )}
      {/* Darkening veil for readability over the scene */}
      <div className="absolute inset-0 bg-gradient-to-b from-base/60 via-base/30 to-base/70" />
    </div>
  );
}
