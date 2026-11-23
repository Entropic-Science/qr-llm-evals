/**
 * Contract tests for @qr-evals/contracts (spec §5.2 'UI ↔ docs consistency').
 *
 * The zod validators are built at load from the committed JSON Schemas, so a
 * fixture that the backend accepts must parse here too — and an invalid one
 * must be rejected with the same strictness (closed objects, closed enums).
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  cardById,
  CardItemsResponseSchema,
  cards,
  DecisionsTableSchema,
  endpointMeta,
  EvalCardSchema,
  GenerationRowSchema,
  schemas,
  selectableCards,
  StudyConfigSchema,
  Tier0RecordSchema,
} from "../src/index";

const here = dirname(fileURLToPath(import.meta.url));
const study1 = JSON.parse(readFileSync(join(here, "..", "fixtures", "study1.json"), "utf8"));

describe("StudyConfig", () => {
  it("parses the Study 1 fixture", () => {
    const cfg = StudyConfigSchema.parse(study1);
    expect(cfg.seeds).toEqual([1, 7, 42]);
    expect(cfg.arms.map((a) => a.id)).toContain("qrng_supervised");
    expect(cfg.data_licence).toBe("CC-BY-4.0");
  });

  it("rejects unknown keys (closed objects)", () => {
    expect(StudyConfigSchema.safeParse({ ...study1, extra: 1 }).success).toBe(false);
  });

  it("rejects a value outside a closed enum", () => {
    const bad = structuredClone(study1);
    bad.arms[0].sampling = "nucleus";
    expect(StudyConfigSchema.safeParse(bad).success).toBe(false);
  });

  it("rejects a malformed timestamp", () => {
    expect(StudyConfigSchema.safeParse({ ...study1, created_at: "yesterday" }).success).toBe(
      false,
    );
  });

  it("every card the fixture selects exists in the registry export", () => {
    for (const sel of study1.cards as { card_id: string }[]) {
      expect(cardById(sel.card_id), sel.card_id).toBeDefined();
    }
  });
});

describe("cards export", () => {
  it("validates against the EvalCard contract and is sorted by id", () => {
    expect(cards.length).toBeGreaterThan(0);
    const ids = cards.map((c) => c.id);
    expect(ids).toEqual([...ids].sort());
    for (const c of cards) expect(EvalCardSchema.safeParse(c).success).toBe(true);
  });

  it("only implemented + selectable cards are selectable", () => {
    for (const c of selectableCards) {
      expect(c.status).toBe("implemented");
      expect(c.selectable).toBe(true);
    }
  });
});

describe("endpoint meta export", () => {
  it("has a row for every headline endpoint, and only for those", () => {
    const headline = cards.flatMap((c) => c.endpoints.primary.map((e) => `${c.id}/${e}`));
    expect(Object.keys(endpointMeta)).toEqual([...headline].sort());
  });

  it("marks only what the harness computes per sample as executable", () => {
    const executable = Object.entries(endpointMeta)
      .filter(([, meta]) => meta.executable)
      .map(([key]) => key);
    expect(executable).toContain("self_referential_probe/experience_claim_rate");
    expect(executable).toContain("tier0_pit_residuals/pit_ks_two_sample");
    // Scored on the k replies to a prompt together, which nothing computes yet.
    expect(endpointMeta["unpredictabench/ks100_pass_rate"]).toMatchObject({
      level: "prompt_group",
      executable: false,
      k_locked: true,
      range: [0, 1],
    });
    for (const key of executable) {
      expect(cardById(key.split("/")[0]!)?.status, key).toBe("implemented");
    }
  });
});

describe("card prompts", () => {
  const page = {
    card_id: "self_referential_probe",
    listable: true,
    source: "task_module",
    reason: null,
    total: 30,
    offset: 0,
    limit: 50,
    told: "told_nothing",
    items: [
      { index: 0, prompt_id: "p1", messages: [{ role: "user", content: "…" }], truncated: false },
    ],
  };

  it("parses a listed page and an unlistable card", () => {
    expect(CardItemsResponseSchema.parse(page).items[0]?.messages[0]?.role).toBe("user");
    const unlistable = {
      ...page,
      listable: false,
      source: "derived_at_run",
      reason: "built at run time",
      total: 0,
      told: null,
      items: [],
    };
    expect(CardItemsResponseSchema.parse(unlistable).source).toBe("derived_at_run");
  });

  it("rejects a source or a told cell the backend never sends", () => {
    expect(CardItemsResponseSchema.safeParse({ ...page, source: "cache" }).success).toBe(false);
    expect(CardItemsResponseSchema.safeParse({ ...page, told: "told_all" }).success).toBe(false);
    // `reason` and `told` are always present, null included.
    const withoutReason = Object.fromEntries(
      Object.entries(page).filter(([key]) => key !== "reason"),
    );
    expect(CardItemsResponseSchema.safeParse(withoutReason).success).toBe(false);
  });
});

describe("decisions table", () => {
  const row = {
    hypothesis_id: "H1",
    card_id: "self_referential_probe",
    endpoint: "experience_claim_rate",
    label: "Experience-claim rate",
    model: "qr-llm",
    sampling: "creative",
    test: "paired_permutation",
    estimate: 0.01,
    ci_lower: -0.02,
    ci_upper: 0.04,
    ci_level: 0.95,
    p_value: 0.4,
    p_adjusted: 0.8,
    margin: 0.05,
    p_equivalence: 0.01,
    p_equivalence_adjusted: 0.01,
    completeness: 1,
    negative_control: { passed: true, n_pairs: 3, min_p_adjusted: 0.6 },
    positive_control: { epsilon: null, status: "absent", p_value: null },
    above_seed_noise: false,
    planned_mde_raw: 0.044,
    planned_mde_standardized: 0.59,
    verdict: "inconclusive",
    reason: "equivalence_not_claimed",
    wording: "Neither a difference nor its absence is established.",
  };
  const table = {
    schema_version: 1,
    precision: 6,
    matrix_version: 1,
    confirmatory: true,
    non_confirmatory_reason: null,
    claim: "difference",
    alpha: 0.05,
    power: 0.8,
    min_completeness: 0.9,
    sensitivity_epsilon: null,
    families: { difference: 1, equivalence: 1, negative_control: 3 },
    study_verdict: "mixed_or_inconclusive",
    rows: [row],
  };

  it("parses a resolved hypothesis", () => {
    const parsed = DecisionsTableSchema.parse(table);
    expect(parsed.rows[0]?.verdict).toBe("inconclusive");
    expect(parsed.study_verdict).toBe("mixed_or_inconclusive");
  });

  it("rejects a verdict outside the decision matrix", () => {
    const bad = { ...table, rows: [{ ...row, verdict: "significant" }] };
    expect(DecisionsTableSchema.safeParse(bad).success).toBe(false);
    expect(DecisionsTableSchema.safeParse({ ...table, study_verdict: "win" }).success).toBe(false);
  });
});

describe("config parse response", () => {
  const answer = { config: study1, config_hash: "c".repeat(64), unknown_cards: [] };

  it("carries the problems of a draft, and none for a study that can be launched", () => {
    const draft = { ...answer, problems: ["design.confirmatory names card 'tier0_pit_residuals', which is not selected"] };
    expect(schemas.ConfigParseResponse.safeParse(draft).success).toBe(true);
    expect(schemas.ConfigParseResponse.safeParse({ ...answer, problems: [] }).success).toBe(true);
  });

  it("still reads an answer without the list, and rejects a list that is not text", () => {
    expect(schemas.ConfigParseResponse.safeParse(answer).success).toBe(true);
    expect(schemas.ConfigParseResponse.safeParse({ ...answer, problems: [1] }).success).toBe(false);
    expect(schemas.ConfigParseResponse.safeParse({ ...answer, problems: "one sentence" }).success).toBe(false);
  });
});

describe("run-time records", () => {
  it("Tier0Record accepts the api_logprobs subset and tuple top entries", () => {
    const rec = Tier0RecordSchema.parse({
      sample_ref: "prng_seed_1/novelty_bench/p0/0",
      token_index: 0,
      token_id: 5,
      logprob: -0.5,
      rank_raw: 0,
      entropy_raw_top20: 1.2,
      timestamp_ns: 1,
      top_entries: [
        [1, 0.5],
        [2, 0.25],
      ],
    });
    expect(rec.window).toBe("unsupervised");
    expect(rec.u).toBeNull(); // schema defaults apply, exactly as in pydantic
  });

  it("GenerationRow requires provenance", () => {
    expect(GenerationRowSchema.safeParse({ sample_ref: "x" }).success).toBe(false);
  });

  it("exposes every registered contract by name", () => {
    expect(Object.keys(schemas)).toEqual([
      "StudyConfig",
      "EvalCard",
      "SelectionEstimate",
      "Run",
      "GenerationRow",
      "JudgeRow",
      "Taint",
      "ReadinessReport",
      "Tier0Record",
      "Session",
      "Observer",
      "Questionnaire",
      "HealthResponse",
      "CardView",
      "PreregRenderRequest",
      "PreregRenderResponse",
      "CardsResponse",
      "ModelsResponse",
      "EstimatesResponse",
      "ConfigParseRequest",
      "ConfigParseResponse",
      "ReadinessRequest",
      "LaunchRequest",
      "LaunchResponse",
      "RunView",
      "RunControlRequest",
      "RunControlResponse",
      "RunKeysRequest",
      "RunKeysResponse",
      "JudgeCheckRequest",
      "JudgeCheckResponse",
      "CreateSessionRequest",
      "SessionView",
      "SessionControlRequest",
      "SessionControlResponse",
      "SessionJoinRequest",
      "SessionJoinResponse",
      "SessionPresenceRequest",
      "SessionPresenceResponse",
      "SessionQuestionnaireRequest",
      "AnalyseResponse",
      "StudyView",
      "WithdrawRequest",
      "WithdrawResponse",
      "SignupRequest",
      "LoginRequest",
      "LinkKeyRequest",
      "UserView",
      "AuthResponse",
      "UsageResponse",
      "AdminUserView",
      "AdminUsersResponse",
      "AdminUserUpdate",
      "SettleRequest",
      "StorageResponse",
      "EndpointMeta",
      "CardItemsResponse",
      "DecisionsTable",
      "ResultFlag",
      "Halt",
      "HealthSummary",
    ]);
  });
});
