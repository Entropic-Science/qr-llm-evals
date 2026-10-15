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
  cards,
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
      "ReadinessRequest",
      "LaunchRequest",
      "LaunchResponse",
      "RunView",
      "RunControlRequest",
      "RunControlResponse",
      "RunKeysRequest",
      "RunKeysResponse",
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
    ]);
  });
});
