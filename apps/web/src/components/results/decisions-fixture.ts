import type { DecisionRow, DecisionsTable } from "@qr-evals/contracts";

import type { ResultsTableData } from "../../lib/results";

/**
 * Test fixtures in the shape `analysis/decisions.py` writes.
 *
 * `designedDecisions` is the `tables/decisions.json` of the designed bundle
 * that `backend/tests/test_decisions.py` analyses end to end (12 prompts of
 * self_referential_probe, α = 0.01, ε = 0.03): two inconclusive token-stream
 * hypotheses and one equivalent paired one. The other rows are that table's
 * rows with the fields a different verdict changes.
 */

const passedNegative = { passed: true, n_pairs: 3, min_p_adjusted: 1 } as const;

export const ksRow: DecisionRow = {
  hypothesis_id: "H1",
  card_id: "tier0_pit_residuals",
  endpoint: "pit_ks_two_sample",
  label: "Token sampling residuals (KS)",
  model: "qr-llm",
  sampling: "creative",
  test: "ks_two_sample",
  estimate: 0.030556,
  ci_lower: null,
  ci_upper: null,
  ci_level: 0.99,
  p_value: 0.688683,
  p_adjusted: 1,
  margin: null,
  p_equivalence: null,
  p_equivalence_adjusted: null,
  completeness: 1,
  negative_control: passedNegative,
  positive_control: { epsilon: 0.03, status: "passed", p_value: 0 },
  above_seed_noise: false,
  planned_mde_raw: null,
  planned_mde_standardized: null,
  verdict: "inconclusive",
  reason: "equivalence_not_testable",
  wording: "Neither a difference nor its absence is established on Token sampling residuals (KS) (this test has no equivalence margin).",
};

export const mmdRow: DecisionRow = {
  ...ksRow,
  hypothesis_id: "H2",
  card_id: "tier0_mmd_hamming",
  endpoint: "mmd_hamming",
  label: "Token sequence divergence (MMD)",
  test: "mmd_hamming_permutation",
  estimate: -0.000871,
  p_value: 0.540846,
  positive_control: { epsilon: 0.03, status: "passed", p_value: 0.0001 },
  wording: "Neither a difference nor its absence is established on Token sequence divergence (MMD) (this test has no equivalence margin).",
};

export const equivalentRow: DecisionRow = {
  hypothesis_id: "H3",
  card_id: "self_referential_probe",
  endpoint: "experience_claim_rate",
  label: "Experience-claim rate",
  model: "qr-llm",
  sampling: "creative",
  test: "paired_permutation",
  estimate: 0.001852,
  ci_lower: -0.010773,
  ci_upper: 0.014477,
  ci_level: 0.99,
  p_value: 1,
  p_adjusted: 1,
  margin: 0.05,
  p_equivalence: 0,
  p_equivalence_adjusted: 0,
  completeness: 1,
  negative_control: passedNegative,
  positive_control: { epsilon: 0.03, status: "passed", p_value: 0.0006 },
  above_seed_noise: false,
  planned_mde_raw: 0.114925,
  planned_mde_standardized: 1.090274,
  verdict: "equivalent",
  reason: null,
  wording: "No difference larger than ±0.05 on Experience-claim rate; the assay detected a sampler bias of ε = 0.03.",
};

export const designedDecisions: DecisionsTable = {
  schema_version: 1,
  precision: 6,
  matrix_version: 1,
  confirmatory: true,
  non_confirmatory_reason: null,
  claim: "difference_or_equivalence",
  alpha: 0.01,
  power: 0.8,
  min_completeness: 0.9,
  sensitivity_epsilon: 0.03,
  families: { difference: 3, equivalence: 1, negative_control: 9 },
  study_verdict: "mixed_or_inconclusive",
  rows: [ksRow, mmdRow, equivalentRow],
};

/** One row per remaining verdict and reason, as the decision matrix words them. */
export const effectRow: DecisionRow = {
  ...equivalentRow,
  estimate: 0.212963,
  ci_lower: 0.151204,
  ci_upper: 0.274722,
  p_value: 0.0001,
  p_adjusted: 0.0003,
  p_equivalence: 0.999871,
  p_equivalence_adjusted: 0.999871,
  above_seed_noise: true,
  verdict: "effect",
  reason: null,
  wording: "Quantum and pseudo-random sampling differ on Experience-claim rate.",
};

export const effectWithinMarginRow: DecisionRow = {
  ...equivalentRow,
  estimate: 0.021296,
  ci_lower: 0.009118,
  ci_upper: 0.033474,
  p_value: 0.0002,
  p_adjusted: 0.0006,
  p_equivalence: 0.000041,
  p_equivalence_adjusted: 0.000041,
  above_seed_noise: true,
  verdict: "effect_within_margin",
  reason: null,
  wording: "A difference on Experience-claim rate is detectable but smaller than ±0.05.",
};

export const incompleteRow: DecisionRow = {
  ...equivalentRow,
  completeness: 0.75,
  verdict: "invalid",
  reason: "incomplete",
  wording: "No conclusion: fewer than 90% of the planned pairs were analysed.",
};

export const negativeControlRow: DecisionRow = {
  ...equivalentRow,
  negative_control: { passed: false, n_pairs: 3, min_p_adjusted: 0.0027, can_fail: true },
  verdict: "invalid",
  reason: "negative_control",
  wording: "No conclusion: two seeds of the same pseudo-random generator differed, or a seed control is missing or could not have failed.",
};

/** No seed pair differs, but with this few prompts none could have: the control did not pass. */
export const cannotFailRow: DecisionRow = {
  ...equivalentRow,
  negative_control: { passed: false, n_pairs: 3, min_p_adjusted: 1, can_fail: false },
  verdict: "invalid",
  reason: "negative_control_cannot_fail",
  wording: "No conclusion: two seeds of the same pseudo-random generator differed, or a seed control is missing or could not have failed.",
};

export const unavailableRow: DecisionRow = {
  ...equivalentRow,
  estimate: null,
  ci_lower: null,
  ci_upper: null,
  p_value: null,
  p_adjusted: null,
  p_equivalence: null,
  p_equivalence_adjusted: null,
  completeness: null,
  negative_control: { passed: false, n_pairs: 3, min_p_adjusted: null },
  positive_control: { epsilon: 0.03, status: "absent", p_value: null },
  above_seed_noise: null,
  verdict: "unavailable",
  reason: "not_produced",
  wording: "Not assessed: the required data were not produced.",
};

export const positiveFailedRow: DecisionRow = {
  ...equivalentRow,
  positive_control: { epsilon: 0.03, status: "failed", p_value: 0.4127 },
  verdict: "inconclusive",
  reason: "positive_control_failed",
  wording: "Neither a difference nor its absence is established on Experience-claim rate (the positive control was not detected).",
};

/** A study that registered a difference claim only and no positive control. */
export const noClaimRow: DecisionRow = {
  ...equivalentRow,
  positive_control: { epsilon: null, status: "absent", p_value: null },
  verdict: "inconclusive",
  reason: "equivalence_not_claimed",
  wording: "Neither a difference nor its absence is established on Experience-claim rate (the study registered no equivalence claim).",
};

export function decisionsWith(rows: DecisionRow[], overrides: Partial<DecisionsTable> = {}): DecisionsTable {
  return { ...designedDecisions, rows, ...overrides };
}

/** The same analysis on a pilot run: every row still resolves, and every sentence says so. */
export function notConfirmatory(reason: NonNullable<DecisionsTable["non_confirmatory_reason"]>): DecisionsTable {
  return {
    ...designedDecisions,
    confirmatory: false,
    non_confirmatory_reason: reason,
    study_verdict: "not_confirmatory",
    rows: designedDecisions.rows.map((row) => ({ ...row, wording: `Not confirmatory: ${row.wording}` })),
  };
}

/** The part of a designed `plan.json` the results page reads. */
export const designedPlan = {
  claim_rule: "Each confirmatory hypothesis resolves by script to one verdict under decision matrix version 1.",
  exclusion_rule: "Tainted samples are excluded.",
  contrasts: [
    { a: ["qrng_unsupervised"], b: ["prng_seed_1", "prng_seed_7", "prng_seed_42"], epsilon: null, kind: "source", model: "qr-llm", sampling: "creative" },
    { a: ["prng_seed_1"], b: ["prng_seed_7"], epsilon: null, kind: "seed_vs_seed", model: "qr-llm", sampling: "creative" },
  ],
  design: {
    claim: "difference_or_equivalence",
    alpha: 0.01,
    sensitivity_epsilon: 0.03,
    families: { difference: 3, equivalence: 1, negative_control: 9 },
    hypotheses: [
      { id: "H1", contrast: 0, sided: "two_sided", statement: "H1: on Token sampling residuals (KS) (tier0_pit_residuals), quantum sampling differs from the pooled tokens of 3 seeded pseudo-random runs (qr-llm · creative)." },
      { id: "H2", contrast: 0, sided: "one_sided_upper", statement: "H2: on Token sequence divergence (MMD) (tier0_mmd_hamming), quantum sampling differs from the pooled tokens of 3 seeded pseudo-random runs (qr-llm · creative)." },
      { id: "H3", contrast: 0, sided: "two_sided", statement: "H3: on Experience-claim rate (self_referential_probe), quantum sampling differs from the per-prompt mean of 3 seeded pseudo-random runs (qr-llm · creative)." },
    ],
  },
};

/**
 * A confirmatory endpoint table of a designed analysis: comparison rows carry
 * `model` and `sampling` and an `equivalence` result, and no `p_adjusted`,
 * `claim_supported` or `significant_at_05`.
 */
export const designedEndpointTable: ResultsTableData = {
  schema_version: 1,
  precision: 6,
  card_id: "self_referential_probe",
  endpoint: "experience_claim_rate",
  tier: 4,
  role: "primary",
  family: "confirmatory",
  available: true,
  correction: "holm",
  test: "paired_permutation",
  special: null,
  comparisons: [
    {
      id: "source:0", kind: "source", model: "qr-llm", sampling: "creative",
      arms_a: ["qrng_unsupervised"], arms_b: ["prng_seed_1", "prng_seed_7", "prng_seed_42"],
      available: true, epsilon: null, effect: 0.001852, mean_diff: 0.001852, ci_level: 0.99, ci_lower: -0.010773, ci_upper: 0.014477,
      p_value: 1, n_pairs: 360, n_prompts: 12,
      equivalence: { margin: 0.05, p_value: 0, ci_level: 0.98, equivalent: true },
    },
    {
      id: "seed_vs_seed:1", kind: "seed_vs_seed", model: "qr-llm", sampling: "creative",
      arms_a: ["prng_seed_1"], arms_b: ["prng_seed_7"],
      available: true, epsilon: null, effect: -0.002778, ci_level: 0.99, ci_lower: -0.021, ci_upper: 0.015444, p_value: 0.8125,
    },
  ],
  controls: [],
  epsilon_curve: [],
};

/** An exploratory table of the same analysis, as the executor writes one it could not compute. */
export const designedExploratoryTable: ResultsTableData = {
  schema_version: 1,
  precision: 6,
  card_id: "tier0_mmd_hamming",
  endpoint: "mmd_hamming_seed_vs_seed",
  tier: 0,
  role: "exploratory",
  family: "tier_0_exploratory",
  available: false,
  reason: "required archived endpoint rows are unavailable",
  correction: "none",
  test: "mmd_hamming_permutation",
  special: null,
  comparisons: [
    {
      id: "source:0", kind: "source", model: "qr-llm", sampling: "creative",
      arms_a: ["qrng_unsupervised"], arms_b: ["prng_seed_1", "prng_seed_7", "prng_seed_42"],
      available: false, epsilon: null, reason: "not a Tier-0 primary",
    },
  ],
  controls: [],
  epsilon_curve: [],
};
