/**
 * Static backdrop for in-app pages. Plain solid fill — no animation,
 * no particles, no mesh gradients.
 */
export default function AmbientBackground() {
  return <div className="fixed inset-0 -z-10 bg-base pointer-events-none" aria-hidden="true" />;
}
