/**
 * The hero scene: a retrieval engine, drawn literally.
 *
 * A faceted core (the FAQ index) sits inside three tilted rings. Message
 * cards ride those rings, and question particles stream inward and get
 * absorbed. It is a picture of what the product does, which is the only
 * reason to put 3D on a landing page at all.
 *
 * Performance notes:
 * - Particles are one InstancedMesh, not 60 draw calls.
 * - dpr is capped at 1.75; beyond that nothing here looks better.
 * - The whole thing freezes for prefers-reduced-motion and drops to a static
 *   render, so the page stays usable for anyone who gets motion sick.
 */

import { RoundedBox } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import * as THREE from "three";

// The brand logo's palette: electric blue, its cyan glow, brushed silver.
const ACCENT = "#1f8fff";
const MINT = "#5cc6ff";
const CARD = "#121926";
const LINE = "#2f3b52";

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(query.matches);
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  return reduced;
}

/* -------------------------------------------------------------------------- */
/* Core                                                                       */
/* -------------------------------------------------------------------------- */

function Core({ still }: { still: boolean }) {
  const wire = useRef<THREE.Mesh>(null);
  const solid = useRef<THREE.Mesh>(null);

  useFrame((_, delta) => {
    if (still) return;
    if (wire.current) {
      wire.current.rotation.y += delta * 0.18;
      wire.current.rotation.x += delta * 0.06;
    }
    if (solid.current) {
      solid.current.rotation.y -= delta * 0.1;
    }
  });

  return (
    <group>
      <mesh ref={wire}>
        <icosahedronGeometry args={[1.15, 1]} />
        <meshBasicMaterial color={ACCENT} wireframe transparent opacity={0.5} />
      </mesh>
      <mesh ref={solid}>
        <icosahedronGeometry args={[0.62, 0]} />
        <meshStandardMaterial
          color="#15181d"
          emissive={ACCENT}
          emissiveIntensity={0.35}
          flatShading
          roughness={0.5}
          metalness={0.2}
        />
      </mesh>
      <pointLight position={[0, 0, 0]} color={ACCENT} intensity={7} distance={5} />
    </group>
  );
}

/* -------------------------------------------------------------------------- */
/* Orbit rings                                                                */
/* -------------------------------------------------------------------------- */

function Ring({
  radius,
  tilt,
  spin,
  still,
}: {
  radius: number;
  tilt: [number, number, number];
  spin: number;
  still: boolean;
}) {
  const ref = useRef<THREE.Mesh>(null);
  useFrame((_, delta) => {
    if (!still && ref.current) ref.current.rotation.z += delta * spin;
  });
  return (
    <mesh ref={ref} rotation={tilt}>
      <torusGeometry args={[radius, 0.004, 8, 128]} />
      <meshBasicMaterial color={LINE} transparent opacity={0.75} />
    </mesh>
  );
}

/* -------------------------------------------------------------------------- */
/* Message cards                                                              */
/* -------------------------------------------------------------------------- */

interface CardSpec {
  radius: number;
  speed: number;
  offset: number;
  y: number;
  scale: number;
  answered: boolean;
}

function MessageCard({ spec, still }: { spec: CardSpec; still: boolean }) {
  const group = useRef<THREE.Group>(null);
  const angle = useRef(spec.offset);

  useFrame((state, delta) => {
    if (!group.current) return;
    if (!still) angle.current += delta * spec.speed;

    const a = angle.current;
    group.current.position.set(
      Math.cos(a) * spec.radius,
      spec.y + Math.sin(a * 1.6) * 0.12,
      Math.sin(a) * spec.radius,
    );
    // Keep cards facing the viewer so they read as UI, not as floating debris.
    group.current.lookAt(state.camera.position);
  });

  const accentLine = spec.answered ? MINT : ACCENT;

  return (
    <group ref={group} scale={spec.scale}>
      <RoundedBox args={[0.92, 0.56, 0.05]} radius={0.07} smoothness={3}>
        <meshStandardMaterial color={CARD} roughness={0.75} metalness={0.05} />
      </RoundedBox>
      {/* The "text" — three bars, the first one accented like a sender tag */}
      <mesh position={[-0.16, 0.13, 0.032]}>
        <boxGeometry args={[0.34, 0.045, 0.01]} />
        <meshBasicMaterial color={accentLine} />
      </mesh>
      <mesh position={[-0.05, 0.01, 0.032]}>
        <boxGeometry args={[0.56, 0.038, 0.01]} />
        <meshBasicMaterial color={LINE} />
      </mesh>
      <mesh position={[-0.14, -0.1, 0.032]}>
        <boxGeometry args={[0.38, 0.038, 0.01]} />
        <meshBasicMaterial color={LINE} />
      </mesh>
    </group>
  );
}

/* -------------------------------------------------------------------------- */
/* Inbound question particles                                                 */
/* -------------------------------------------------------------------------- */

const PARTICLE_COUNT = 48;

function Particles({ still }: { still: boolean }) {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const dummy = useMemo(() => new THREE.Object3D(), []);

  // Each particle: a direction on the sphere plus how far along it is.
  const seeds = useMemo(
    () =>
      Array.from({ length: PARTICLE_COUNT }, () => {
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(2 * Math.random() - 1);
        return {
          dir: new THREE.Vector3(
            Math.sin(phi) * Math.cos(theta),
            Math.sin(phi) * Math.sin(theta) * 0.55,
            Math.cos(phi),
          ).normalize(),
          t: Math.random(),
          speed: 0.1 + Math.random() * 0.16,
        };
      }),
    [],
  );

  useFrame((_, delta) => {
    if (!mesh.current) return;

    for (let i = 0; i < seeds.length; i++) {
      const seed = seeds[i];
      if (!still) {
        seed.t += delta * seed.speed;
        if (seed.t > 1) seed.t -= 1; // absorbed by the core, respawn outside
      }

      // Travel from the outside in.
      const distance = 3.4 - seed.t * 2.5;
      dummy.position.copy(seed.dir).multiplyScalar(distance);

      // Shrink as they get close, so they read as being consumed.
      const scale = 0.035 * (0.35 + (1 - seed.t) * 0.65);
      dummy.scale.setScalar(scale);
      dummy.updateMatrix();
      mesh.current.setMatrixAt(i, dummy.matrix);
    }
    mesh.current.instanceMatrix.needsUpdate = true;
  });

  return (
    <instancedMesh
      ref={mesh}
      args={[undefined, undefined, PARTICLE_COUNT]}
      frustumCulled={false}
    >
      <sphereGeometry args={[1, 8, 8]} />
      <meshBasicMaterial color={MINT} transparent opacity={0.65} />
    </instancedMesh>
  );
}

/* -------------------------------------------------------------------------- */
/* Scene                                                                      */
/* -------------------------------------------------------------------------- */

function Scene({ still }: { still: boolean }) {
  const group = useRef<THREE.Group>(null);

  const cards: CardSpec[] = useMemo(
    () => [
      { radius: 1.9, speed: 0.24, offset: 0, y: 0.45, scale: 0.8, answered: true },
      { radius: 2.25, speed: -0.18, offset: 2.1, y: -0.5, scale: 0.68, answered: false },
      { radius: 1.7, speed: 0.3, offset: 4.0, y: -0.15, scale: 0.62, answered: true },
      { radius: 2.45, speed: -0.14, offset: 5.4, y: 0.75, scale: 0.56, answered: false },
      { radius: 2.1, speed: 0.2, offset: 3.1, y: -0.85, scale: 0.54, answered: true },
    ],
    [],
  );

  // Gentle parallax. Damped rather than snapped, or it feels twitchy.
  useFrame((state, delta) => {
    if (!group.current || still) return;
    const targetY = state.pointer.x * 0.28;
    const targetX = -state.pointer.y * 0.18;
    group.current.rotation.y += (targetY - group.current.rotation.y) * delta * 2;
    group.current.rotation.x += (targetX - group.current.rotation.x) * delta * 2;
  });

  return (
    <>
      <ambientLight intensity={0.55} />
      <directionalLight position={[4, 5, 3]} intensity={1.1} />
      <directionalLight position={[-5, -2, -3]} intensity={0.4} color={MINT} />

      <group ref={group}>
        <Core still={still} />

        <Ring radius={1.75} tilt={[Math.PI / 2.4, 0.3, 0]} spin={0.08} still={still} />
        <Ring radius={2.2} tilt={[Math.PI / 1.8, -0.4, 0.5]} spin={-0.06} still={still} />
        <Ring radius={2.65} tilt={[Math.PI / 3, 0.9, 0.2]} spin={0.04} still={still} />

        {cards.map((spec, i) => (
          <MessageCard key={i} spec={spec} still={still} />
        ))}

        <Particles still={still} />
      </group>
    </>
  );
}

/** True while `ref` is anywhere near the viewport. */
function useOnScreen(ref: RefObject<HTMLElement>): boolean {
  const [onScreen, setOnScreen] = useState(true);

  useEffect(() => {
    const node = ref.current;
    if (!node || typeof IntersectionObserver === "undefined") return;

    const observer = new IntersectionObserver(
      ([entry]) => setOnScreen(entry.isIntersecting),
      // A little margin so it is already running by the time it scrolls in.
      { rootMargin: "200px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [ref]);

  return onScreen;
}

export default function HeroScene() {
  const reduced = usePrefersReducedMotion();
  const holder = useRef<HTMLDivElement>(null);
  const onScreen = useOnScreen(holder);

  // Rendering 60fps of a scene nobody can see is just heat. Once the hero
  // scrolls away the loop stops entirely and the canvas keeps its last frame;
  // reduced-motion users get a single static render.
  const frameloop = !onScreen ? "never" : reduced ? "demand" : "always";

  return (
    <div ref={holder} style={{ width: "100%", height: "100%" }}>
      <Canvas
        camera={{ position: [0, 0.6, 6.8], fov: 42 }}
        dpr={[1, 1.75]}
        frameloop={frameloop}
        gl={{ antialias: true, alpha: true }}
        // Nothing here reacts to clicks, so let pointer events fall through to
        // the page. Otherwise the canvas silently eats text selection.
        style={{ pointerEvents: "none" }}
      >
        <Scene still={reduced} />
      </Canvas>
    </div>
  );
}
