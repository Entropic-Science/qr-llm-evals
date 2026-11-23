// Seeded randomness for everything drawn by hand. A stroke must come out the same on every render, pan and edit,
// so nothing here reads Math.random: the seed is the only input.

/** The mulberry32 generator: a function returning the next float in [0, 1). Same seed, same sequence. */
export function mulberry32(seed: number) {
  let a = seed;
  return () => {
    let t = (a += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * A stable 32-bit seed for a node or edge id (FNV-1a over the id's UTF-16 code units, low byte then high byte, so
 * an ASCII id hashes exactly as its bytes would).
 */
export function seedFromId(id: string): number {
  let hash = 0x811c9dc5;
  const mix = (byte: number) => { hash = Math.imul(hash ^ byte, 0x01000193); };
  for (let i = 0; i < id.length; i++) {
    const unit = id.charCodeAt(i);
    mix(unit & 0xff);
    if (unit > 0xff) mix(unit >>> 8);
  }
  return hash >>> 0;
}
