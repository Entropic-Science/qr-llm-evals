import { useEffect, useRef } from "react";
import mark from "../assets/logo-pattern-128.webp";

// The Entropic Science mark, shared with entropic.science: the woven hexagon, with the same idle drift as the main
// site's header (a smoothed random walk, about half a degree and a fraction of a pixel). The drift writes straight to
// the element, so it never re-renders React, and it does not run at all under prefers-reduced-motion.
const WOBBLE_DEG = 0.5;
const TRANSLATION_PX = 0.15;
const SMOOTHING = 0.99;

function useDrift() {
  const ref = useRef<HTMLImageElement | null>(null);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof window.matchMedia !== "function" || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let angle = 0, tx = 0, ty = 0, frame = 0;
    const sample = () => (Math.random() * 2 - 1) * (1 - SMOOTHING);
    const tick = () => {
      angle = angle * SMOOTHING + sample() * WOBBLE_DEG;
      tx = tx * SMOOTHING + sample() * TRANSLATION_PX;
      ty = ty * SMOOTHING + sample() * TRANSLATION_PX;
      node.style.transform = `translate(${tx.toFixed(2)}px, ${ty.toFixed(2)}px) rotate(${angle.toFixed(2)}deg)`;
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, []);
  return ref;
}

export function Logo({ size = 40, tag }: { size?: number; tag?: string }) {
  const ref = useDrift();
  return <span className="brand__lockup">
    <img ref={ref} className="brand__mark" src={mark} alt="" width={size} height={size} />
    <span className="brand__name">Entropic Science</span>
    {tag ? <span className="brand__tag">{tag}</span> : null}
  </span>;
}
