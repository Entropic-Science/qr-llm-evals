import type { EndpointMeta, SampleSize } from "@qr-evals/contracts";

// Can the planned number of prompts carry a hypothesis? Closed-form bounds only.
//
// A line-for-line mirror of `backend/src/qr_llm_evals/cards/power.py`, checked against
// `packages/contracts/fixtures/power.json`. The setup pages use it for same-frame feedback and offline; what a
// study registers is always the server's `plan.design`, so these numbers are shown as a preview.
//
// The unit of analysis of a paired test is the prompt. Repetitions (k) and told cells sharpen each prompt's
// value; they add no units. Everything here is therefore a function of `n`, the number of prompts.

export type Level = EndpointMeta["level"];

/** Power every rating and every recommended sample size is computed for. */
export const POWER = 0.80;
/** Standardized effect an endpoint without a raw-unit margin is rated against. */
export const REFERENCE_EFFECT = 0.30;
/** Share of the planned pairs that must be analysed for a verdict to stand. */
export const MIN_COMPLETENESS = 0.90;
/** A declared margin may not exceed this share of the endpoint's range. */
export const MARGIN_CAP_FRACTION = 0.10;
/** Nor be below this share of it: no eval has the prompts to detect less. */
export const MARGIN_FLOOR_FRACTION = 0.001;
/** Below this power at the margin a sample size is rated insufficient. */
export const MARGINAL_POWER = 0.50;
/** Each of the two one-sided equivalence tests needs this power for 0.80 overall. */
export const EQUIVALENCE_SIDE_POWER = 0.90;
/** Decimals kept in a registered number, as the backend rounds them before hashing. */
export const PRECISION = 12;

/**
 * The error function. JavaScript has none, and the backend's is libm's. This is the series
 * erf(x) = 2/sqrt(pi) * exp(-x^2) * sum of 2^n x^(2n+1) / (1 * 3 * ... * (2n+1)): every term is positive, so
 * nothing cancels and the result is good to about 1e-15 over the whole range.
 */
function erf(x: number): number {
  const a = Math.abs(x);
  // Beyond 6 the result differs from 1 by less than 3e-17, which a double cannot hold.
  if (a >= 6) return Math.sign(x);
  let term = a;
  let sum = a;
  for (let n = 1; term > sum * 1e-17; n++) {
    term *= (2 * a * a) / (2 * n + 1);
    sum += term;
  }
  return Math.sign(x) * (2 / Math.sqrt(Math.PI)) * Math.exp(-a * a) * sum;
}

/** The standard normal distribution function, as Python's `NormalDist().cdf`. */
function cdf(x: number): number {
  return 0.5 * (1.0 + erf(x / Math.SQRT2));
}

/**
 * The standard normal quantile of `p`: Wichura's algorithm AS241 (PPND16), the one Python's
 * `NormalDist().inv_cdf` uses, with its coefficients.
 */
export function z(p: number): number {
  if (!(p > 0.0 && p < 1.0)) throw new RangeError("p must be in the range 0.0 < p < 1.0");
  const q = p - 0.5;
  if (Math.abs(q) <= 0.425) {
    const r = 0.180625 - q * q;
    const num = (((((((2.5090809287301226727e+3 * r +
                       3.3430575583588128105e+4) * r +
                       6.7265770927008700853e+4) * r +
                       4.5921953931549871457e+4) * r +
                       1.3731693765509461125e+4) * r +
                       1.9715909503065514427e+3) * r +
                       1.3314166789178437745e+2) * r +
                       3.3871328727963666080e+0) * q;
    const den = (((((((5.2264952788528545610e+3 * r +
                       2.8729085735721942674e+4) * r +
                       3.9307895800092710610e+4) * r +
                       2.1213794301586595867e+4) * r +
                       5.3941960214247511077e+3) * r +
                       6.8718700749205790830e+2) * r +
                       4.2313330701600911252e+1) * r +
                       1.0);
    return num / den;
  }
  let r = q <= 0.0 ? p : 1.0 - p;
  r = Math.sqrt(-Math.log(r));
  let num: number;
  let den: number;
  if (r <= 5.0) {
    r = r - 1.6;
    num = (((((((7.74545014278341407640e-4 * r +
                 2.27238449892691845833e-2) * r +
                 2.41780725177450611770e-1) * r +
                 1.27045825245236838258e+0) * r +
                 3.64784832476320460504e+0) * r +
                 5.76949722146069140550e+0) * r +
                 4.63033784615654529590e+0) * r +
                 1.42343711074968357734e+0);
    den = (((((((1.05075007164441684324e-9 * r +
                 5.47593808499534494600e-4) * r +
                 1.51986665636164571966e-2) * r +
                 1.48103976427480074590e-1) * r +
                 6.89767334985100004550e-1) * r +
                 1.67638483018380384940e+0) * r +
                 2.05319162663775882187e+0) * r +
                 1.0);
  } else {
    r = r - 5.0;
    num = (((((((2.01033439929228813265e-7 * r +
                 2.71155556874348757815e-5) * r +
                 1.24266094738807843860e-3) * r +
                 2.65321895265761230930e-2) * r +
                 2.96560571828504891230e-1) * r +
                 1.78482653991729133580e+0) * r +
                 5.46378491116411436990e+0) * r +
                 6.65790464350110377720e+0);
    den = (((((((2.04426310338993978564e-15 * r +
                 1.42151175831644588870e-7) * r +
                 1.84631831751005468180e-5) * r +
                 7.86869131145613259100e-4) * r +
                 1.48753612908506148525e-2) * r +
                 1.36929880922735805310e-1) * r +
                 5.99832206555887937690e-1) * r +
                 1.0);
  }
  const x = num / den;
  return q < 0.0 ? -x : x;
}

/** The fewest prompts at which a sign-flip test can reach `alpha / m` at all. */
export function nFloor(alpha: number, m: number): number {
  return Math.floor(1 + Math.log2(m / alpha)) + 1;
}

/**
 * The fewest prompts the harness accepts for a seed-against-seed control.
 *
 * The control is one Holm family over every seed pair of every hypothesis (`mControl` tests), so a pair fails
 * only at `p < alpha / mControl`. The floor is one prompt above the exact bound of `nFloor`: the analysis
 * estimates the p-value by Monte Carlo, and at the exact bound the estimate lands on either side of the level.
 * One prompt below the floor a pair that differs as much as the outcome allows often still fails, so "cannot
 * fail" is true only further down. The floor is where the harness stops taking the risk: a control that cannot
 * fail passes whatever the seeds do, so this errs on the side of more prompts.
 */
export function controlFloor(alpha: number, mControl: number): number {
  return nFloor(alpha, mControl) + 1;
}

/** The smallest effect, in standard deviations, detected with `POWER`. */
export function mdeStandardized(n: number, alpha: number, m: number): number {
  return (z(1 - alpha / (2 * m)) + z(POWER)) / Math.sqrt(n);
}

/**
 * Worst-case standard deviation of one prompt's quantum-minus-control difference.
 *
 * A value in `[lo, hi]` has a standard deviation of at most half the range. The quantum side is the mean of
 * `k * toldCells` independent values per prompt: every request of a quantum arm draws fresh entropy. The
 * control side is the mean over `pooledSeeds` seeded runs, and each run counts `k` independent values, not
 * `k * toldCells`: the told cells of a seeded run share one seed per prompt and repetition, so in the worst
 * case they repeat one another. Without the told factor the variance is `(1 + 1 / pooledSeeds) / k`.
 */
export function sdBound(lo: number, hi: number, level: Level, k: number, toldCells: number, pooledSeeds: number): number {
  if (level === "token_stream") throw new RangeError("a test on the pooled token stream has no per-prompt variance bound");
  if (hi <= lo) throw new RangeError(`range must have hi > lo, got (${lo}, ${hi})`);
  const variance = level === "sample" ? 1 / (k * toldCells) + 1 / (k * pooledSeeds) : 1 + 1 / pooledSeeds;
  return ((hi - lo) / 2) * Math.sqrt(variance);
}

/** Power to detect a raw `effect` on `n` prompts at the corrected level. */
export function powerAt(effect: number, sd: number, n: number, alpha: number, m: number): number {
  return cdf((effect * Math.sqrt(n)) / sd - z(1 - alpha / (2 * m)));
}

/** Prompts needed to detect a standardized effect with `POWER`. */
export function promptsNeeded(effectOverSd: number, alpha: number, m: number): number {
  const needed = Math.ceil(((z(1 - alpha / (2 * m)) + z(POWER)) / effectOverSd) ** 2);
  // The backend refuses where a float overflows; here that is infinity.
  if (!Number.isFinite(needed)) throw new RangeError(`an effect of ${effectOverSd} standard deviations is too small to plan for`);
  return needed;
}

/**
 * The fewest repetitions in `[kFrom, kTo]` at which `n` prompts detect `margin`; `null` if none gives `POWER`.
 *
 * Repetitions add no units, but they shrink the worst-case variance of each prompt's value, so they are the
 * way out when an eval has no more prompts.
 */
export function kNeeded(
  lo: number, hi: number, margin: number, n: number, toldCells: number, pooledSeeds: number, alpha: number, m: number,
  kFrom: number, kTo: number,
): number | null {
  for (let k = kFrom; k <= kTo; k++) {
    if (powerAt(margin, sdBound(lo, hi, "sample", k, toldCells, pooledSeeds), n, alpha, m) >= POWER) return k;
  }
  return null;
}

/** The smallest margin `n` prompts can confirm when the true difference is zero. */
export function equivalenceFloor(sd: number, n: number, alpha: number, mE: number): number {
  return ((z(1 - alpha / mE) + z(EQUIVALENCE_SIDE_POWER)) * sd) / Math.sqrt(n);
}

// Python's round(value, 12). The two can only disagree on an exact tie, and a double is never exactly halfway
// between two 12-decimal numbers.
function registered(value: number): number {
  return Number(value.toFixed(PRECISION));
}

/** The arguments of `rate`, named as the backend's keywords so a fixture row passes straight through. */
export interface RateArgs {
  level: Level;
  range: readonly [number, number] | null;
  margin: number | null;
  n: number;
  dataset_items: number;
  k: number | null;
  told_cells: number;
  pooled_seeds: number;
  alpha: number;
  m: number;
  m_e: number;
  /** The size of the seed-against-seed family: every seed pair of every hypothesis. 0 without a seed pair. */
  m_control: number;
  /** The eval's own number of repetitions, and the most a study may set. */
  k_default: number | null;
  k_max: number | null;
}

/**
 * Rate `n` prompts for one hypothesis in a family of `m` difference tests.
 *
 * `m_e` is the size of the equivalence family. Only a margin together with a range can earn `sufficient`:
 * without both there is no raw-unit effect to have power for. Below `controlFloor` the hypothesis could only
 * resolve to invalid, so no number of prompts under it is rated above `insufficient` or recommended.
 *
 * `k_recommended` is the fewest repetitions from `k_default` up to `k_max` at which all the prompts of the
 * eval are enough for the margin. It does not depend on `k` or `n`, so following it does not change it.
 */
export function rate({
  level, range, margin, n, dataset_items, k, told_cells, pooled_seeds, alpha, m, m_e, m_control, k_default, k_max,
}: RateArgs): SampleSize {
  const floor = nFloor(alpha, m);
  if (level === "token_stream") {
    return {
      n,
      n_floor: floor,
      control_floor: null,
      mde_standardized: null,
      sd_bound: null,
      mde_raw: null,
      power_at_margin: null,
      equivalence_floor: null,
      n_recommended: null,
      reachable: null,
      k_recommended: null,
      rating: "unknown",
      basis: "no_closed_form",
    };
  }
  const controls = m_control >= 1 ? controlFloor(alpha, m_control) : null;
  const least = controls === null ? floor : Math.max(floor, controls);
  // A card whose prompts are only known at run time plans zero of them.
  const mde = n >= 1 ? mdeStandardized(n, alpha, m) : null;
  let sd: number | null = null;
  let power: number | null = null;
  let smallestMargin: number | null = null;
  let repetitions: number | null = null;
  let rating: SampleSize["rating"];
  let basis: SampleSize["basis"];
  let recommended: number;
  if (margin !== null && range !== null) {
    basis = "margin_worst_case";
    sd = sdBound(range[0], range[1], level, k !== null ? k : 1, told_cells, pooled_seeds);
    power = powerAt(margin, sd, n, alpha, m);
    if (m_e >= 1 && n >= 1) smallestMargin = equivalenceFloor(sd, n, alpha, m_e);
    recommended = Math.max(least, promptsNeeded(margin / sd, alpha, m));
    if (level === "sample" && k_default !== null && dataset_items >= least) {
      repetitions = kNeeded(
        range[0], range[1], margin, dataset_items, told_cells, pooled_seeds, alpha, m,
        k_default, Math.max(k_default, k_max !== null ? k_max : k_default),
      );
    }
    rating = power >= POWER ? "sufficient" : power >= MARGINAL_POWER ? "marginal" : "insufficient";
  } else {
    basis = "standardized_reference";
    recommended = Math.max(least, promptsNeeded(REFERENCE_EFFECT, alpha, m));
    rating = mde !== null && mde <= REFERENCE_EFFECT ? "marginal" : "insufficient";
  }
  if (n < least) rating = "insufficient";
  return {
    n,
    n_floor: floor,
    control_floor: controls,
    mde_standardized: mde === null ? null : registered(mde),
    sd_bound: sd === null ? null : registered(sd),
    mde_raw: sd === null || mde === null ? null : registered(mde * sd),
    power_at_margin: power === null ? null : registered(power),
    equivalence_floor: smallestMargin === null ? null : registered(smallestMargin),
    n_recommended: recommended,
    reachable: recommended <= dataset_items,
    k_recommended: repetitions,
    rating,
    basis,
  };
}
