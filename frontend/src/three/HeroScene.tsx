/**
 * The hero scene: the Nexora emblem, working.
 *
 * At the centre is the brand's own mark, lifted out of the wordmark's "O": a
 * four-point star inside a brushed-silver ring, with the blue crescent sweeping
 * around it and the outer halo with its two sparkles from the full logo.
 * Message cards orbit it, and question sparks stream inward and are absorbed
 * by the star. It is the same picture of what the product does, drawn in the
 * brand's shapes instead of a generic globe.
 *
 * Performance notes:
 * - Particles are one InstancedMesh, not 60 draw calls.
 * - The glow is a single additive sprite, not a post-processing pass.
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
const SILVER = "#d6dde8";
const CARD = "#121926";
const LINE = "#2f3b52";

/** The Nexora four-point star: long points, softly concave sides. */
function starShape(size: number, pinch = 0.16): THREE.Shape {
  const k = size * pinch;
  const shape = new THREE.Shape();
  shape.moveTo(0, size);
  shape.quadraticCurveTo(k, k, size, 0);
  shape.quadraticCurveTo(k, -k, 0, -size);
  shape.quadraticCurveTo(-k, -k, -size, 0);
  shape.quadraticCurveTo(-k, k, 0, size);
  return shape;
}

/** A soft radial glow, drawn once into a canvas and reused as a sprite. */
function useGlowTexture(): THREE.Texture {
  return useMemo(() => {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 128;
    const ctx = canvas.getContext("2d")!;
    const g = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
    g.addColorStop(0, "rgba(140, 210, 255, 1)");
    g.addColorStop(0.25, "rgba(31, 143, 255, 0.55)");
    g.addColorStop(1, "rgba(31, 143, 255, 0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 128, 128);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    return texture;
  }, []);
}

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
/* Emblem                                                                     */
/* -------------------------------------------------------------------------- */

function Emblem({ still }: { still: boolean }) {
  const emblem = useRef<THREE.Group>(null);
  const star = useRef<THREE.Mesh>(null);
  const crescent = useRef<THREE.Mesh>(null);
  const halo = useRef<THREE.Group>(null);
  const glow = useRef<THREE.Sprite>(null);
  const glowTexture = useGlowTexture();

  const starGeometry = useMemo(() => {
    const geometry = new THREE.ExtrudeGeometry(starShape(0.62), {
      depth: 0.1,
      bevelEnabled: true,
      bevelThickness: 0.05,
      bevelSize: 0.035,
      bevelSegments: 4,
      curveSegments: 24,
    });
    geometry.center();
    return geometry;
  }, []);

  const sparkGeometry = useMemo(() => new THREE.ShapeGeometry(starShape(0.11, 0.2), 12), []);

  useFrame((state, delta) => {
    if (still) return;
    const t = state.clock.elapsedTime;
    // The star turns in full 3D, so its bevels catch the light as it goes.
    if (star.current) star.current.rotation.y += delta * 0.6;
    // The crescent sweeps around inside the ring, like the swirl in the logo.
    if (crescent.current) crescent.current.rotation.z -= delta * 0.9;
    if (halo.current) halo.current.rotation.z += delta * 0.05;
    // The whole emblem floats and sways a little.
    if (emblem.current) {
      emblem.current.position.y = Math.sin(t * 0.8) * 0.06;
      emblem.current.rotation.x = Math.sin(t * 0.5) * 0.12;
      emblem.current.rotation.y = Math.sin(t * 0.35) * 0.18;
    }
    if (glow.current) {
      const pulse = 3.1 + Math.sin(t * 1.6) * 0.18;
      glow.current.scale.set(pulse, pulse, 1);
    }
  });

  return (
    <group ref={emblem}>
      {/* Glow behind the star */}
      <sprite ref={glow} scale={[3.1, 3.1, 1]} position={[0, 0, -0.3]}>
        <spriteMaterial
          map={glowTexture}
          transparent
          opacity={0.38}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </sprite>

      {/* The star */}
      <mesh ref={star} geometry={starGeometry}>
        <meshStandardMaterial
          color="#9fd8ff"
          emissive={ACCENT}
          emissiveIntensity={0.42}
          metalness={0.7}
          roughness={0.18}
        />
      </mesh>

      {/* The silver ring of the "O" */}
      <mesh>
        <torusGeometry args={[1.05, 0.11, 32, 160]} />
        <meshStandardMaterial color={SILVER} metalness={0.75} roughness={0.28} />
      </mesh>

      {/* The blue crescent sweeping inside it */}
      <mesh ref={crescent}>
        <torusGeometry args={[0.84, 0.045, 16, 120, Math.PI * 0.85]} />
        <meshStandardMaterial color={MINT} emissive={ACCENT} emissiveIntensity={1.4} toneMapped={false} />
      </mesh>

      {/* The outer halo with its two sparkles, from the full logo */}
      <group ref={halo}>
        <mesh>
          <torusGeometry args={[1.55, 0.012, 8, 200]} />
          <meshBasicMaterial color={ACCENT} transparent opacity={0.85} toneMapped={false} />
        </mesh>
        <mesh geometry={sparkGeometry} position={[1.55, 0, 0.01]}>
          <meshBasicMaterial color={MINT} toneMapped={false} side={THREE.DoubleSide} />
        </mesh>
        <mesh geometry={sparkGeometry} position={[-1.55, 0, 0.01]}>
          <meshBasicMaterial color={MINT} toneMapped={false} side={THREE.DoubleSide} />
        </mesh>
      </group>

      <pointLight position={[0, 0, 0.9]} color={ACCENT} intensity={6} distance={5} />
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
      <torusGeometry args={[radius, 0.004, 8, 160]} />
      <meshBasicMaterial color={ACCENT} transparent opacity={0.28} />
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
        if (seed.t > 1) seed.t -= 1; // absorbed by the star, respawn outside
      }

      // Travel from the outside in.
      const distance = 3.4 - seed.t * 3.1;
      dummy.position.copy(seed.dir).multiplyScalar(distance);

      // Shrink as they get close, so they read as being consumed.
      const scale = 0.035 * (0.35 + (1 - seed.t) * 0.65);
      // Stretched into a thin spark rather than a round dot.
      dummy.scale.set(scale, scale * 1.9, scale * 0.4);
      dummy.rotation.z = seed.t * Math.PI * 2;
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
      <octahedronGeometry args={[1, 0]} />
      <meshBasicMaterial color={MINT} transparent opacity={0.8} toneMapped={false} />
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
      { radius: 2.0, speed: 0.22, offset: 0, y: 0.75, scale: 0.68, answered: true },
      { radius: 2.2, speed: -0.17, offset: 2.1, y: -0.8, scale: 0.6, answered: false },
      { radius: 1.9, speed: 0.27, offset: 4.0, y: -0.25, scale: 0.54, answered: true },
      { radius: 2.3, speed: -0.13, offset: 5.4, y: 1.05, scale: 0.5, answered: false },
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
      <ambientLight intensity={0.5} />
      {/* A white key light and blue rim lights, so the silver reads as metal */}
      <directionalLight position={[3, 4, 5]} intensity={2.2} color="#ffffff" />
      <directionalLight position={[-4, -3, 2]} intensity={1.2} color={MINT} />
      <directionalLight position={[0, 2, -5]} intensity={0.8} color={ACCENT} />

      <group ref={group}>
        <Emblem still={still} />

        <Ring radius={2.2} tilt={[Math.PI / 2.3, 0.35, 0]} spin={0.08} still={still} />
        <Ring radius={2.65} tilt={[Math.PI / 1.75, -0.45, 0.5]} spin={-0.05} still={still} />

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
        camera={{ position: [0, 0.4, 7.2], fov: 42 }}
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
