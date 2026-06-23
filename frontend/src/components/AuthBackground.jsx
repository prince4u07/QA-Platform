import { Suspense, lazy, useState } from 'react';
import SceneErrorBoundary from './SceneErrorBoundary';

// Code-split: three.js only downloads when a capable viewport actually renders it.
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

// Decide once, at mount, whether this device should run the 3D scene.
function detectCapable() {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const small = window.matchMedia('(max-width: 767px)').matches;
  return !reduce && !small && supportsWebGL();
}

/**
 * Full-bleed animated backdrop for the auth pages.
 * - Always paints the indigo/teal mesh gradient (also the fallback).
 * - Layers the 3D particle scene on top only when the device can handle it
 *   (not small-screen, not prefers-reduced-motion).
 */
export default function AuthBackground() {
  const [enable3D] = useState(detectCapable);

  return (
    <div className="absolute inset-0 -z-10 overflow-hidden bg-base">
      {/* Animated mesh gradient — always present, doubles as the static fallback */}
      <div className="absolute inset-0 bg-brand-radial animate-mesh-drift" />

      {enable3D && (
        <SceneErrorBoundary>
          <Suspense fallback={null}>
            <ParticleScene />
          </Suspense>
        </SceneErrorBoundary>
      )}

      {/* Readability veil so glass cards keep AA contrast over the scene */}
      <div className="absolute inset-0 bg-gradient-to-t from-base via-base/50 to-base/20" />
    </div>
  );
}
