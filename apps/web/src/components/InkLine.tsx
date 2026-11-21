// A hand-drawn underline, after entropic.science's <InkLine>: a seeded pen stroke through a few anchors, joined as a
// Catmull-Rom curve so it bends like a pen instead of zigzagging. Same seed, same line. The draw-in is a CSS
// animation (tokens.css, `.ink-line`), so reduced motion gets the finished stroke with no script involved.
function mulberry32(seed: number) {
  let a = seed;
  return () => {
    let t = (a += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function inkLinePath(seed: number, anchors: number): string {
  const count = Math.max(3, Math.round(anchors));
  const random = mulberry32(seed);
  const points = Array.from({ length: count }, (_, index): [number, number] => {
    const y = random() * 2 - 1;
    random(); random(); // The source also draws a wobble period and phase per anchor; skipped here, kept in step.
    return [(index * 100) / (count - 1), y];
  });
  const at = (index: number) => points[Math.min(count - 1, Math.max(0, index))]!;
  const f = (value: number) => value.toFixed(2);
  let d = `M${f(at(0)[0])} ${f(at(0)[1])}`;
  for (let i = 0; i < count - 1; i++) {
    const [p0, p1, p2, p3] = [at(i - 1), at(i), at(i + 1), at(i + 2)];
    d += ` C${f(p1[0] + (p2[0] - p0[0]) / 6)} ${f(p1[1] + (p2[1] - p0[1]) / 6)} ${f(p2[0] - (p3[0] - p1[0]) / 6)} ${f(p2[1] - (p3[1] - p1[1]) / 6)} ${f(p2[0])} ${f(p2[1])}`;
  }
  return d;
}

/** About one pen anchor per 1.8 characters of label, as on the main site. */
export function InkLine({ label, seed = 7 }: { label: string; seed?: number }) {
  const anchors = Math.min(24, Math.max(6, Math.round(label.length * 0.55)));
  return <svg className="ink-line" viewBox="0 -5 100 10" preserveAspectRatio="none" aria-hidden="true"><path d={inkLinePath(seed, anchors)} pathLength={1} /></svg>;
}
