import { useRef } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { Float, Icosahedron } from '@react-three/drei';

// Static starfield positions — generated once at module load (not during render,
// so it stays pure and stable across re-renders).
const PARTICLE_COUNT = 2000;
const PARTICLE_POSITIONS = (() => {
  const arr = new Float32Array(PARTICLE_COUNT * 3);
  for (let i = 0; i < PARTICLE_COUNT; i++) {
    const r = 4 + Math.random() * 7;
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    arr[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    arr[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    arr[i * 3 + 2] = r * Math.cos(phi);
  }
  return arr;
})();

// Drifting starfield of points distributed in a spherical shell.
function Particles() {
  const ref = useRef();

  useFrame((_, delta) => {
    if (!ref.current) return;
    ref.current.rotation.y += delta * 0.04;
    ref.current.rotation.x += delta * 0.012;
  });

  return (
    <points ref={ref} frustumCulled={false}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[PARTICLE_POSITIONS, 3]} />
      </bufferGeometry>
      <pointsMaterial
        size={0.05}
        color="#8b93ff"
        transparent
        opacity={0.85}
        sizeAttenuation
        depthWrite={false}
      />
    </points>
  );
}

function WireShape({ position, color, scale }) {
  return (
    <Float speed={1.1} rotationIntensity={0.6} floatIntensity={1.1}>
      <Icosahedron args={[1, 0]} position={position} scale={scale}>
        <meshBasicMaterial color={color} wireframe transparent opacity={0.22} />
      </Icosahedron>
    </Float>
  );
}

// Subtle mouse parallax — camera eases toward the pointer.
function ParallaxRig() {
  useFrame((state) => {
    const { camera, pointer } = state;
    camera.position.x += (pointer.x * 1.4 - camera.position.x) * 0.03;
    camera.position.y += (pointer.y * 1.4 - camera.position.y) * 0.03;
    camera.lookAt(0, 0, 0);
  });
  return null;
}

export default function ParticleScene() {
  return (
    <Canvas
      camera={{ position: [0, 0, 12], fov: 60 }}
      dpr={[1, 1.5]}
      gl={{ antialias: true, alpha: true, powerPreference: 'high-performance' }}
      style={{ position: 'absolute', inset: 0 }}
    >
      <Particles />
      <WireShape position={[-4.5, 2, -2]} color="#6366f1" scale={1.5} />
      <WireShape position={[5, -1.5, -3]} color="#14b8a6" scale={1.1} />
      <WireShape position={[2.5, 3, -4]} color="#38bdf8" scale={0.8} />
      <ParallaxRig />
    </Canvas>
  );
}
