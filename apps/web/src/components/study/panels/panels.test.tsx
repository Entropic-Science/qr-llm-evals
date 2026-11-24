import { cards, tokenLimits, type Arm, type ModelRef } from "@qr-evals/contracts";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { vi } from "vitest";
import studyOneJson from "../../../../../../packages/contracts/fixtures/study1.json";
import { setMargin, toggleConfirmatory } from "../../../lib/config-edits";
import { parseStudyConfig, studyOneTemplate, type WizardStudyConfig } from "../../../lib/config-store";
import { observedArmInFixedPlan } from "../../../lib/map/fixed-plan";
import { PANELS, type PanelDef } from "../../../lib/panels";
import { rate } from "../../../lib/recommendations";
import { rosterFromArms } from "../../../lib/roster";
import { studySnapshot, toStudyYaml } from "../../../lib/study-file";
import { FieldScope, fieldId } from "../../../routes/configure/fields/Field";
import { panelId } from "./Panel";

// A panel of Study 1 is a large page for jsdom, and the gate runs this file beside forty others: the default five seconds is too tight.
vi.setConfig({ testTimeout: 30_000 });

const QR: ModelRef = { id: "qr-llm", family: "qr_llm", provider: "qr_server", served_name: "qr-llm", checkpoint: null };
const panel = (id: string): PanelDef => {
  const found = PANELS.find((candidate) => candidate.id === id);
  if (!found) throw new Error(`no panel ${id}`);
  return found;
};

/** Study 1 as a loaded legacy file: no design block. */
function legacy(): WizardStudyConfig {
  const parsed = parseStudyConfig(studyOneJson);
  if (!parsed) throw new Error("study1.json does not match StudyConfig.");
  return parsed;
}

/** A study whose arms no roster stands for: one arm on the system generator. */
function byHand(): WizardStudyConfig {
  const config = studyOneTemplate();
  return { ...config, arms: [...config.arms, { id: "system", model: "qr-llm", sampling: "creative", source: "prng_system" }] };
}

/** The panels over one study held in state, as the setup shell holds it. `latest` is the study after the last edit. */
function mount(ids: readonly string[], initial: WizardStudyConfig = studyOneTemplate()) {
  const state = { latest: initial, edits: 0 };
  function Shell() {
    const [config, setConfig] = useState(initial);
    const update = (next: WizardStudyConfig) => { state.latest = next; state.edits += 1; setConfig(next); };
    const recs = rate(config, cards);
    return <>{ids.map((id) => { const { Component } = panel(id); return <Component key={id} config={config} updateConfig={update} cards={cards} modelOptions={[QR]} recs={recs} />; })}</>;
  }
  const view = render(<Shell />);
  return { ...view, state };
}

const group = (name: string) => screen.getByRole("group", { name });

describe("every panel", () => {
  it.each([
    ["the Study 1 template", studyOneTemplate],
    ["a legacy study without a design block", legacy],
    ["a study with hand-written arms", byHand],
    ["a study with no evals, no comparison and no supervised sessions", (): WizardStudyConfig => ({ ...legacy(), arms: [], cards: [], supervised: null })],
  ])("renders for %s as a fieldset with its legend", (_name, make) => {
    const { container } = mount(PANELS.map((item) => item.id), make());
    for (const item of PANELS) {
      const element = container.querySelector(`#${panelId(item.id)}`);
      expect(element, item.id).not.toBeNull();
      expect(element!.tagName).toBe("FIELDSET");
      expect(element).toHaveClass("settings-panel");
      expect(element!.querySelector(":scope > legend")).toHaveTextContent(item.label);
    }
    expect(container.querySelectorAll("fieldset.settings-panel")).toHaveLength(PANELS.length);
  });

  it("gives each field one id on the page, and a prefixed one inside a scope", () => {
    const config = studyOneTemplate();
    const props = { config, updateConfig: () => undefined, cards, modelOptions: [QR], recs: rate(config, cards) };
    const { container } = render(<>
      {PANELS.map(({ id, Component }) => <Component key={id} {...props} />)}
      <FieldScope.Provider value="settings-">{PANELS.map(({ id, Component }) => <Component key={id} {...props} />)}</FieldScope.Provider>
    </>);
    const ids = [...container.querySelectorAll("[id]")].map((element) => element.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(container.querySelector(`#${fieldId("title")}`)).not.toBeNull();
    expect(container.querySelector(`#${fieldId("title", "settings-")}`)).not.toBeNull();
    expect(container.querySelector(`#${panelId("judge", "settings-")}`)).not.toBeNull();
    expect(fieldId("design.margins.self_referential_probe/experience_claim_rate")).toBe("field-design-margins-self-referential-probe-experience-claim-rate");
  });

  it("never passes on a study it was not asked to change", () => {
    const { state } = mount(PANELS.map((item) => item.id));
    expect(state.edits).toBe(0);
  });
});

describe("every key a panel claims", () => {
  // What the study records and no control sets, each for a reason. A key that is not listed here has to have
  // a control that works: a claim alone does not make a setting editable.
  const READ_ONLY: Readonly<Record<string, string>> = {
    schema_version: "the version of the study file format, set by the harness",
    template: "the file the study started from",
    data_licence: "one licence for every study on the harness",
    "design.version": "the version of the design block, set by the harness",
    "judge.provider": "the judge is always a Claude model",
    "judge.rubric_hash": "each eval's rubric has its own hash, recorded with every judgement",
    "supervised.target_pool_hash": "the server's asset hashes are not offered to the page",
    "supervised.questionnaires_hash": "the server's asset hashes are not offered to the page",
  };
  // Keys that one control sets together with another, by the key whose control it is.
  const SET_BY: Readonly<Record<string, string>> = {
    "design.confirmatory[].card_id": "design.confirmatory",
    "design.confirmatory[].endpoint": "design.confirmatory",
    "design.margins[].card_id": "design.margins[].margin",
    "design.margins[].endpoint": "design.margins[].margin",
    // The comparisons panel rewrites the arms from roles, and the models, the seed list and the session block with them.
    models: "arms", seeds: "arms", supervised: "arms",
    ...Object.fromEntries(["id", "family", "provider", "served_name", "checkpoint"].map((key) => [`narrative_model.${key}`, "narrative_model"])),
  };
  const CONTROL = "input:not([disabled]):not([type='hidden']), select:not([disabled]), textarea:not([disabled]), button:not([disabled]):not(.rec)";
  /** The study key a `data-path` stands for: a margin's field, a roster field, the keys of an open map. */
  const keyOf = (dataPath: string) => (dataPath.startsWith("design.margins.") ? "design.margins[].margin" : dataPath.startsWith("roster.") ? "arms" : dataPath);

  /** A study with every block, and a margin declared on an outcome that takes one. */
  function full(): WizardStudyConfig {
    const meq = { card_id: "altered_state_meq", endpoint: "meq30_total" };
    return setMargin(toggleConfirmatory(studyOneTemplate(), meq), meq, 0.05);
  }

  it("has a control that works, or is listed as recorded and not set", () => {
    const { container } = mount(PANELS.map((item) => item.id), full());
    const missing: string[] = [];
    for (const item of PANELS) {
      const element = container.querySelector<HTMLElement>(`#${panelId(item.id)}`)!;
      const fields = [...element.querySelectorAll<HTMLElement>("[data-path]")];
      for (const claimed of item.paths) {
        const target = SET_BY[claimed] ?? claimed;
        const own = fields.filter((field) => keyOf(field.dataset.path ?? "") === (target.endsWith(".*") ? target.slice(0, -2) : target));
        if (Object.hasOwn(READ_ONLY, claimed)) {
          // Recorded, not set: it is shown, as a value, and nothing in its field takes input.
          expect(own.some((field) => field.classList.contains("field--readonly")), `${claimed} is shown read-only`).toBe(true);
          expect(own.flatMap((field) => [...field.querySelectorAll(CONTROL)]), `${claimed} has no control`).toEqual([]);
          continue;
        }
        if (!own.some((field) => field.matches(CONTROL) || field.querySelector(CONTROL) !== null)) missing.push(`${item.id}: ${claimed}`);
      }
    }
    expect(missing, "keys a panel claims and gives no working control for").toEqual([]);
  });

  it("lists as read-only only keys that a panel claims", () => {
    const claimed = new Set(PANELS.flatMap((item) => item.paths));
    for (const key of Object.keys(READ_ONLY)) expect(claimed, key).toContain(key);
    for (const key of [...Object.keys(SET_BY), ...Object.values(SET_BY)]) expect([...claimed].some((path) => path === key || key.startsWith(path) || path.startsWith(key)), key).toBe(true);
  });

  it("shows a k or a prompt count the study carries for an eval that takes none, with the way back to the eval's own", () => {
    const stray = (cardId: string, overrides: { k?: number; items?: number }) => (config: WizardStudyConfig): WizardStudyConfig => ({
      ...config, cards: config.cards.map((selection) => (selection.card_id === cardId ? { ...selection, overrides } : selection)),
    });
    // target_hit_ranking takes no k, and the free logprob endpoints have no prompts of their own: neither had a row to see such a value in.
    const { state } = mount(["prompts"], stray("tier0_logprob_endpoints", { items: 5 })(stray("target_hit_ranking", { k: 7 })(studyOneTemplate())));
    fireEvent.click(screen.getByText(/Evals that do not decide the study/));
    const row = (id: string) => screen.getAllByRole("row").find((candidate) => within(candidate).queryByText(id, { selector: "code" }))!;
    expect(within(row("target_hit_ranking")).getByText(/set in this study, but this eval takes no repetitions/)).toBeInTheDocument();
    fireEvent.click(within(row("target_hit_ranking")).getByRole("button", { name: "Use the card's value" }));
    expect(state.latest.cards.find((selection) => selection.card_id === "target_hit_ranking")?.overrides?.k ?? null).toBeNull();
    expect(within(row("tier0_logprob_endpoints")).getByText(/set in this study, but this eval has no prompts of its own and takes no prompt count/)).toBeInTheDocument();
    fireEvent.click(within(row("tier0_logprob_endpoints")).getByRole("button", { name: "Use the card's value" }));
    expect(state.latest.cards.find((selection) => selection.card_id === "tier0_logprob_endpoints")?.overrides?.items ?? null).toBeNull();
  });
});

describe("study panels", () => {
  it("edit the title, the authors and the contact", () => {
    const { state } = mount(["title", "people"]);
    fireEvent.change(screen.getByLabelText("Study title"), { target: { value: "A new study" } });
    fireEvent.change(screen.getByLabelText("Authors"), { target: { value: "Ada\n\nGrace \n" } });
    fireEvent.change(screen.getByLabelText("Contact"), { target: { value: "ada@example.org" } });
    expect(state.latest).toMatchObject({ title: "A new study", authors: ["Ada", "Grace"], contact: "ada@example.org" });
  });
});

describe("the comparisons panel", () => {
  it("ticks a second preset as a comparison of its own", () => {
    const { state } = mount(["comparison"]);
    fireEvent.click(screen.getByLabelText("qr-llm · Temperature 1 + min-p 0.10"));
    expect(rosterFromArms(state.latest.arms)?.cells).toEqual([{ model: "qr-llm", sampling: "creative" }, { model: "qr-llm", sampling: "t1_minp" }]);
    expect(state.latest.arms).toHaveLength(16);
    expect(screen.getByText("Each ticked pair is its own comparison. Pairs are never pooled.")).toBeInTheDocument();
  });

  it("sets the number of control seeds within two to five", () => {
    const { state } = mount(["comparison"]);
    const seeds = screen.getByLabelText("Control seeds");
    fireEvent.change(seeds, { target: { value: "4" } });
    expect(rosterFromArms(state.latest.arms)?.seeds).toEqual([1, 7, 42, 2]);
    fireEvent.change(seeds, { target: { value: "1" } });
    fireEvent.change(seeds, { target: { value: "9" } });
    expect(rosterFromArms(state.latest.arms)?.seeds).toHaveLength(4);
  });

  it("cycles a positive control from absent to planned to on", () => {
    const { state } = mount(["comparison"]);
    const level = () => state.latest.arms.find((arm) => arm.source === "prng_epsilon" && arm.epsilon === 0.5);
    fireEvent.click(screen.getByRole("button", { name: "ε 0.50: absent. Switch to planned, off." }));
    expect(level()).toMatchObject({ availability: "unavailable", study_seed: 1001 });
    expect(level()!.unavailable_reason).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "ε 0.50: planned, off. Switch to on." }));
    expect(level()).toMatchObject({ availability: "available", unavailable_reason: null });
    fireEvent.click(screen.getByRole("button", { name: "ε 0.50: on. Switch to absent." }));
    expect(level()).toBeUndefined();
    expect(parseStudyConfig(state.latest)).not.toBeNull();
  });

  it("says of the observed arm what the study's own plan does with it: tested by the fixed plan, exploratory in a design", () => {
    const tested = "Adds an observed quantum arm. The fixed Study 1 plan tests it against the unobserved quantum arm of the same comparison on the primary outcomes of that plan, corrected with Holm in a family of their own. In a comparison without an unobserved quantum arm it is tested against the seeded controls instead.";
    const note = () => screen.getByLabelText("Supervised sessions").closest(".field")!.querySelector(".field-note")!;
    mount(["comparison"], legacy());
    expect(note().textContent).toBe(tested);
    expect(note().textContent).not.toMatch(/exploratory|verdict/);
    // The map says the same of the arm of this study, in the case it is in.
    const arms = legacy().arms;
    const observed = arms.find((arm) => arm.observer_mode === "supervised")!;
    expect(tested).toContain(observedArmInFixedPlan(observed, arms.filter((arm) => arm.model === observed.model && arm.sampling === observed.sampling)).story.replace("the unobserved quantum arm on", "the unobserved quantum arm of the same comparison on"));
    cleanup();
    mount(["comparison"]);
    expect(note().textContent).toBe("Adds an observed quantum arm. Its comparison is exploratory and gets no verdict.");
  });

  it("switches supervised sessions off with their arm and their settings", () => {
    const { state } = mount(["comparison", "sessions"]);
    expect(screen.getByLabelText("Intention timer (min)")).toHaveValue(3);
    fireEvent.click(screen.getByLabelText("Supervised sessions"));
    expect(state.latest.supervised).toBeNull();
    expect(state.latest.arms.some((arm) => arm.observer_mode === "supervised")).toBe(false);
    expect(screen.getByText("This study has no supervised sessions. They are switched on under Comparisons.")).toBeInTheDocument();
  });

  it("changes a seed value, and waits while the value typed is another control seed", () => {
    const { state } = mount(["comparison"]);
    const second = screen.getByLabelText("Seed 2");
    fireEvent.change(second, { target: { value: "42" } });
    expect(state.edits).toBe(0);
    fireEvent.change(second, { target: { value: "8" } });
    expect(rosterFromArms(state.latest.arms)?.seeds).toEqual([1, 8, 42]);
    expect(state.latest.arms.map((arm) => arm.id)).toContain("prng_seed_8");
  });

  it("shows hand-written arms read-only and rebuilds them from roles on request", () => {
    const { state } = mount(["comparison"], byHand());
    expect(screen.getByText("These arms were written by hand. Rebuild from roles to edit them here.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Control seeds")).toBeNull();
    expect(within(screen.getByRole("table")).getByText("system")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Rebuild from roles" }));
    expect(state.latest.arms.some((arm) => arm.id === "system")).toBe(false);
    expect(rosterFromArms(state.latest.arms)).not.toBeNull();
    expect(screen.getByLabelText("Control seeds")).toHaveValue(3);
  });
});

describe("the arm table", () => {
  const row = (name: string) => group(name);

  it("edits every field of an arm", () => {
    const initial = studyOneTemplate();
    const { state } = mount(["arm-table"], initial);
    const arm = () => state.latest.arms[2]!;
    const fields = within(row("prng_seed_1"));
    fireEvent.change(fields.getByLabelText("Model"), { target: { value: "qr-llm" } });
    fireEvent.change(fields.getByLabelText("Sampling"), { target: { value: "t1_minp" } });
    fireEvent.change(fields.getByLabelText("Source"), { target: { value: "prng_epsilon" } });
    fireEvent.change(fields.getByLabelText("Seed"), { target: { value: "77" } });
    fireEvent.change(fields.getByLabelText("Bias ε"), { target: { value: "0.5" } });
    fireEvent.change(fields.getByLabelText("Observer mode"), { target: { value: "supervised" } });
    fireEvent.change(fields.getByLabelText("Runs"), { target: { value: "unavailable" } });
    fireEvent.change(fields.getByLabelText("Reason it is switched off"), { target: { value: "not ready" } });
    fireEvent.click(fields.getByLabelText("Deterministic"));
    fireEvent.change(fields.getByLabelText("Arm id"), { target: { value: "bent_by_hand" } });
    const expected: Arm = {
      id: "bent_by_hand", model: "qr-llm", sampling: "t1_minp", source: "prng_epsilon", study_seed: 77, epsilon: 0.5,
      deterministic: false, observer_mode: "supervised", availability: "unavailable", unavailable_reason: "not ready",
    };
    expect(arm()).toEqual(expected);
    expect(Object.keys(expected).sort()).toEqual(panel("arm-table").paths.map((path) => path.replace("arms[].", "")).sort());
    // The other arms are the objects they were.
    expect(state.latest.arms.filter((_, index) => index !== 2)).toEqual(initial.arms.filter((_, index) => index !== 2));
    expect(state.latest.arms[0]).toBe(initial.arms[0]);
    expect(screen.getByRole("group", { name: "bent_by_hand" })).toBeInTheDocument();
  });

  it("clears a seed, a bias and a reason back to none", () => {
    const { state } = mount(["arm-table"]);
    const fields = within(row("prng_epsilon_010"));
    fireEvent.change(fields.getByLabelText("Seed"), { target: { value: "" } });
    fireEvent.change(fields.getByLabelText("Bias ε"), { target: { value: "" } });
    fireEvent.change(fields.getByLabelText("Reason it is switched off"), { target: { value: "" } });
    expect(state.latest.arms.at(-1)).toMatchObject({ id: "prng_epsilon_010", study_seed: null, epsilon: null, unavailable_reason: null });
  });

  it("adds an arm with an id of its own and removes one", () => {
    const { state } = mount(["arm-table"]);
    fireEvent.click(screen.getByRole("button", { name: "Add arm" }));
    expect(state.latest.arms).toHaveLength(9);
    expect(state.latest.arms.at(-1)).toEqual({
      id: "arm_9", model: "qr-llm", sampling: "creative", source: "prng_seeded", study_seed: 1, epsilon: null,
      deterministic: false, observer_mode: "unsupervised", availability: "available", unavailable_reason: null,
    });
    expect(parseStudyConfig(state.latest)).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Remove prng_seed_7" }));
    expect(state.latest.arms.map((arm) => arm.id)).toEqual([
      "qrng_supervised", "qrng_unsupervised", "prng_seed_1", "prng_seed_42", "prng_epsilon_001", "prng_epsilon_003", "prng_epsilon_010", "arm_9",
    ]);
  });

  it("edits the arms a roster cannot stand for", () => {
    const { state } = mount(["arm-table"], byHand());
    fireEvent.change(within(row("system")).getByLabelText("Sampling"), { target: { value: "greedy" } });
    expect(state.latest.arms.at(-1)).toMatchObject({ id: "system", sampling: "greedy", source: "prng_system" });
  });
});

describe("the other advanced panels", () => {
  it("edit the models, the seed list, the estimate assumptions and the pilot", () => {
    const { state } = mount(["models", "seed-list", "estimates", "pilot"]);
    fireEvent.change(within(group("qr-llm")).getByLabelText("Checkpoint"), { target: { value: "org/model@abc" } });
    expect(state.latest.models[0]).toMatchObject({ id: "qr-llm", checkpoint: "org/model@abc" });
    fireEvent.click(screen.getByRole("button", { name: "Add a model by hand" }));
    expect(state.latest.models).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Remove model 2" }));
    expect(state.latest.models).toHaveLength(1);
    // The checkpoint is typed by the researcher; nothing promises to freeze it later.
    expect(within(group("qr-llm")).getByLabelText("Checkpoint")).toHaveAttribute("placeholder", "org/model@revision");
    expect(document.body.textContent).not.toMatch(/frozen at readiness/);

    // A model that arms run on cannot be removed from under them, and renaming it renames it in the arms too.
    expect(screen.getByRole("button", { name: "Remove qr-llm" })).toBeDisabled();
    expect(within(group("qr-llm")).getByText(/8 arms run on this model, so it cannot be removed here/)).toBeInTheDocument();
    fireEvent.change(within(group("qr-llm")).getByLabelText("Model id"), { target: { value: "qr-llm-v2" } });
    expect(state.latest.models[0]!.id).toBe("qr-llm-v2");
    expect(new Set(state.latest.arms.map((arm) => arm.model))).toEqual(new Set(["qr-llm-v2"]));
    expect(state.latest.narrative_model.id).toBe("qr-llm-v2");
    expect(parseStudyConfig(state.latest)).not.toBeNull();
    fireEvent.change(within(group("qr-llm-v2")).getByLabelText("Model id"), { target: { value: "qr-llm" } });
    expect(new Set(state.latest.arms.map((arm) => arm.model))).toEqual(new Set(["qr-llm"]));

    const seeds = screen.getByLabelText("Seed list");
    fireEvent.change(seeds, { target: { value: "1, 7, 42, 1001, x" } });
    expect(state.latest.seeds).toEqual([1, 7, 42, 1001]);
    fireEvent.change(seeds, { target: { value: "1, 7, 42, 1001, 5" } });
    expect(state.latest.seeds).toEqual([1, 7, 42, 1001, 5]);

    fireEvent.change(within(group("Generated tokens per second, by model")).getByLabelText("qr-llm"), { target: { value: "250.5" } });
    fireEvent.change(screen.getByLabelText("Reading speed, tokens per second"), { target: { value: "30" } });
    fireEvent.change(screen.getByLabelText("Streams assumed in a tiled session"), { target: { value: "6" } });
    fireEvent.change(screen.getByLabelText("Session length assumed, in minutes"), { target: { value: "45" } });
    expect(state.latest.estimates).toEqual({ tps_by_model: { "qr-llm": 250.5 }, reading_tps: 30, tiled_streams: 6, session_min: 45 });

    // The label says what the setting does: it chooses the kind of run the launch dialog starts on. It does not
    // say that a pilot is offered or withheld, which the dialog does not do.
    expect(screen.queryByLabelText("Offer a pilot run at launch")).toBeNull();
    fireEvent.click(screen.getByLabelText("Preselect a pilot run in the launch dialog"));
    fireEvent.change(screen.getByLabelText("Pilot prompts"), { target: { value: "50" } });
    expect(state.latest.pilot).toEqual({ enabled: false, prompts: 50 });
    expect(screen.getByText(/The launch dialog offers both kinds of run whatever this says; this chooses which one is selected when it opens\./)).toBeInTheDocument();
    expect(screen.getByText(/A pilot runs the study as configured and is labelled non-confirmatory\. It does not set margins\./)).toBeInTheDocument();
    expect(parseStudyConfig(state.latest)).not.toBeNull();
  });

  it("show what is recorded and not set, and edit the session displays and the narrative model", () => {
    const second: ModelRef = { ...QR, id: "qr-llm-2", served_name: "qr-llm-2" };
    const { state, container } = mount(["record", "display-settings", "narrative"], { ...studyOneTemplate(), models: [QR, second] });
    const record = container.querySelector(`#${panelId("record")}`)!;
    // One thing here can be set: the configuration date, which the template would otherwise carry into every study.
    expect([...record.querySelectorAll("input, select, textarea, button")].map((control) => control.textContent)).toEqual(["Set to now"]);
    expect(record).toHaveTextContent("CC-BY-4.0");
    expect(record).toHaveTextContent("study1");
    expect(record).toHaveTextContent("2026-09-14T00:00:00Z");
    vi.useFakeTimers({ now: new Date("2026-11-22T15:04:05.678Z"), toFake: ["Date"] });
    try { fireEvent.click(within(record as HTMLElement).getByRole("button", { name: "Set to now" })); } finally { vi.useRealTimers(); }
    expect(state.latest.created_at).toBe("2026-11-22T15:04:05+00:00");
    expect(record).toHaveTextContent("2026-11-22T15:04:05+00:00");
    expect(parseStudyConfig(state.latest)).not.toBeNull();
    // A hash the study does not carry is called what it is, with what follows from it.
    expect(record).toHaveTextContent("not pinned");
    expect(record.textContent).not.toMatch(/not frozen yet/);

    fireEvent.change(within(group("Four-stream tile")).getByLabelText("Streams"), { target: { value: "6" } });
    fireEvent.click(within(group("Single stream")).getByLabelText("Show thinking"));
    expect(state.latest.supervised?.settings_by_display).toEqual({
      single: { session_min: 30, break_min: 10, streams: 1, text_size: "md", show_thinking: false },
      tiled: { session_min: 30, break_min: 10, streams: 6, text_size: "sm", show_thinking: false },
    });
    fireEvent.change(screen.getByLabelText("Narrative model"), { target: { value: "qr-llm-2" } });
    expect(state.latest.narrative_model).toEqual(second);
  });
});

describe("the measure panels", () => {
  it("select and deselect an eval, and badge what can decide a study", () => {
    const { state } = mount(["evals"]);
    const novelty = screen.getByRole("checkbox", { name: /^novelty_bench/ });
    expect(novelty).toBeChecked();
    fireEvent.click(novelty);
    expect(state.latest.cards.some((selection) => selection.card_id === "novelty_bench")).toBe(false);
    // The heading is the one the map gives the same three audits, and it claims no more than they do: they
    // read the tokens of the replies the study generates, not every token.
    const always = screen.getByRole("region", { name: "Measured on the tokens" });
    expect(within(always).getByRole("heading", { name: "Measured on the tokens" })).toBeInTheDocument();
    expect(within(always).getByText("These audits read the tokens the study generates; they have no prompts of their own.")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/every token/i);
    expect(within(always).queryByRole("checkbox")).toBeNull();
    expect(within(always).getAllByText("Can decide a study")).toHaveLength(2);
    expect(screen.getByRole("checkbox", { name: /^self_referential_probe/ }).closest("label")).toHaveTextContent("Can decide a study");
    expect(screen.getByRole("checkbox", { name: /^unpredictabench/ }).closest("label")).toHaveTextContent("Not computable yet: scored per prompt group");
    expect(screen.getByRole("checkbox", { name: /^introspection_scales/ }).closest("label")).toHaveTextContent("Descriptive only");
  });

  it("rate the prompts of a deciding outcome as they are typed, and restore the recommended value", () => {
    const { state } = mount(["prompts"]);
    const prompts = screen.getByLabelText("Prompts for self_referential_probe");
    const deciding = () => within(screen.getByRole("table", { name: "Prompts and repetitions of the outcomes that decide the study" }));
    const chip = () => deciding().getByRole("button", { name: /^Prompts: \d+ prompts\./ });
    expect(prompts).toHaveValue(30);
    expect(chip()).toHaveClass("rec--sufficient");

    fireEvent.change(prompts, { target: { value: "6" } });
    expect(state.latest.cards.find((selection) => selection.card_id === "self_referential_probe")?.overrides).toMatchObject({ items: 6 });
    expect(chip()).toHaveClass("rec--insufficient");
    expect(screen.getByText("Uses the first 6 prompts in file order.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Use recommended" }));
    expect(state.latest.cards.find((selection) => selection.card_id === "self_referential_probe")?.overrides?.items ?? null).toBeNull();
    expect(chip()).toHaveClass("rec--sufficient");
    expect(screen.queryByRole("button", { name: "Use recommended" })).toBeNull();
    // The button is gone; the focus is on the chip that now says the value is enough, not dropped on the page.
    expect(document.activeElement).toBe(chip());
  });

  it("say that a measure on the generated tokens has no prompts and no tokens of its own, instead of counting zero or claiming every token", () => {
    mount(["prompts"]);
    const deciding = within(screen.getByRole("table", { name: "Prompts and repetitions of the outcomes that decide the study" }));
    const row = deciding.getByRole("rowheader", { name: /Token sampling residuals/ }).closest("tr")!;
    expect(within(row).getByRole("cell", { name: "no prompts of its own" })).toBeInTheDocument();
    // The chip names what the test is run on: the tokens the study generates, pooled.
    expect(within(row).getByRole("button", { name: /^Prompts: generated tokens\./ })).toBeInTheDocument();
    expect(row).not.toHaveTextContent(/every token/i);
    expect(row).toHaveTextContent("no tokens of its own");
    expect(row).not.toHaveTextContent("0 tokens / arm");
    expect(deciding.getByRole("rowheader", { name: /Experience-claim rate/ }).closest("tr")).toHaveTextContent("864K tokens / arm");
  });

  it("set k within the card's bounds and lock it where the outcome fixes it", () => {
    const { state } = mount(["prompts"]);
    fireEvent.change(screen.getByLabelText("Repetitions (k) for self_referential_probe"), { target: { value: "25" } });
    expect(state.latest.cards.find((selection) => selection.card_id === "self_referential_probe")?.overrides).toMatchObject({ k: 25 });
    expect(screen.getByLabelText("Repetitions (k) for unpredictabench")).toBeDisabled();
    expect(screen.getByText("Prompts are the unit of analysis. Repetitions sharpen each prompt's value, so fewer repetitions call for more prompts; they are never counted as prompts themselves.")).toBeInTheDocument();
  });
});

describe("the token limits panel", () => {
  const PROBE = "self_referential_probe";
  const table = () => within(screen.getByRole("table", { name: "Token limits of the selected evals" }));
  const row = (id: string) => table().getAllByRole("row").find((candidate) => within(candidate).queryByText(id, { selector: "code" }))!;
  const limitOf = (config: WizardStudyConfig, id: string) => config.cards.find((selection) => selection.card_id === id)?.overrides?.max_tokens;
  const hasKey = (config: WizardStudyConfig, id: string) => Object.hasOwn(config.cards.find((selection) => selection.card_id === id)?.overrides ?? {}, "max_tokens");
  const preset = (id: string, name: RegExp) => within(row(id)).getByRole("button", { name });

  it("lists every selected eval that generates replies with its own limit, and says what a limit does and does not do", () => {
    mount(["token-limits"]);
    const template = studyOneTemplate();
    const limited = template.cards.map((selection) => selection.card_id).filter((id) => tokenLimits[id] !== undefined);
    expect(limited.length).toBeGreaterThan(5);
    expect(table().getAllByRole("row")).toHaveLength(limited.length + 1);
    expect(table().getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Eval", "Token limit", "Presets", "At most, per arm"]);
    // An eval that generates nothing has no row.
    expect(tokenLimits.target_hit_ranking).toBeUndefined();
    expect(row("target_hit_ranking")).toBeUndefined();
    const input = screen.getByLabelText(`Token limit for ${PROBE}`);
    expect(input).toHaveValue(800);
    expect(input).toHaveAttribute("min", "400");
    expect(input).toHaveAttribute("max", "64000");
    expect(preset(PROBE, /^Standard/)).toHaveTextContent("Standard · 800");
    expect(preset(PROBE, /^Safe/)).toHaveTextContent("Safe · 3,200");
    expect(preset(PROBE, /^Standard/)).toHaveAttribute("aria-pressed", "true");
    expect(preset(PROBE, /^Safe/)).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("Each reply ends when it reaches its eval's token limit, and a reply that does is flagged as cut. Standard is the eval's own limit. Safe is four times that, at least 256 and at most 64,000: a reply should rarely reach it, but nothing guarantees that, and a model that thinks before it answers can use all of it. The limit caps a reply. It does not change the estimate, which rests on the usual length of a reply, so the last column is the most an arm can generate on that eval.")).toBeInTheDocument();
  });

  it("sets the limit typed, and removes the override when the eval's own limit is typed", () => {
    const { state } = mount(["token-limits"]);
    const before = JSON.stringify(studyOneTemplate());
    const input = screen.getByLabelText(`Token limit for ${PROBE}`);
    fireEvent.change(input, { target: { value: "1000" } });
    expect(limitOf(state.latest, PROBE)).toBe(1000);
    expect(preset(PROBE, /^Standard/)).toHaveAttribute("aria-pressed", "false");
    expect(preset(PROBE, /^Safe/)).toHaveAttribute("aria-pressed", "false");
    fireEvent.change(input, { target: { value: "800" } });
    expect(hasKey(state.latest, PROBE)).toBe(false);
    expect(JSON.stringify(state.latest)).toBe(before);
    expect(toStudyYaml(state.latest)).toBe(toStudyYaml(studyOneTemplate()));
  });

  it("sets an eval to its safe limit and back to its standard one, which deletes the key", () => {
    const { state } = mount(["token-limits"]);
    const before = JSON.stringify(studyOneTemplate());
    fireEvent.click(preset(PROBE, /^Safe/));
    expect(limitOf(state.latest, PROBE)).toBe(3200);
    expect(screen.getByLabelText(`Token limit for ${PROBE}`)).toHaveValue(3200);
    expect(preset(PROBE, /^Safe/)).toHaveAttribute("aria-pressed", "true");
    expect(preset(PROBE, /^Standard/)).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(preset(PROBE, /^Standard/));
    expect(hasKey(state.latest, PROBE)).toBe(false);
    expect(JSON.stringify(state.latest)).toBe(before);
    expect(preset(PROBE, /^Standard/)).toHaveAttribute("aria-pressed", "true");
  });

  it("sets all evals to safe and all back to standard, which leaves the study as it was", () => {
    const { state } = mount(["token-limits"]);
    const template = studyOneTemplate();
    const limited = template.cards.map((selection) => selection.card_id).filter((id) => tokenLimits[id] !== undefined);
    fireEvent.click(screen.getByRole("button", { name: "Set all to safe" }));
    expect(state.edits).toBe(1);
    for (const id of limited) expect(limitOf(state.latest, id), id).toBe(tokenLimits[id]!.safe);
    expect(state.latest.cards.filter((selection) => hasKey(state.latest, selection.card_id)).map((selection) => selection.card_id)).toEqual(limited);
    expect(table().getAllByRole("button", { name: /^Safe/, pressed: true })).toHaveLength(limited.length);
    fireEvent.click(screen.getByRole("button", { name: "Set all to standard" }));
    expect(JSON.stringify(state.latest)).toBe(JSON.stringify(template));
    expect(table().getAllByRole("button", { name: /^Standard/, pressed: true })).toHaveLength(limited.length);
  });

  it("states the most an arm can generate on an eval: prompts × repetitions × told cells × the limit", () => {
    const { state } = mount(["token-limits"]);
    // 30 prompts × k = 24 × three told cells × 800 tokens.
    expect(within(row(PROBE)).getByRole("cell", { name: "1.73M tokens" })).toBeInTheDocument();
    fireEvent.click(preset(PROBE, /^Safe/));
    expect(within(row(PROBE)).getByRole("cell", { name: "6.91M tokens" })).toBeInTheDocument();
    expect(limitOf(state.latest, PROBE)).toBe(3200);
    cleanup();
    // Fewer prompts and repetitions of the study count; an eval the study leaves alone counts with its card's own.
    const fewer = studyOneTemplate();
    mount(["token-limits"], { ...fewer, cards: fewer.cards.map((selection) => (selection.card_id === PROBE ? { ...selection, overrides: { k: 20, items: 10 } } : selection)) });
    expect(within(row(PROBE)).getByRole("cell", { name: "480K tokens" })).toBeInTheDocument();
    const guessing = cards.find((card) => card.id === "source_guessing")!;
    const most = guessing.dataset.items * (guessing.k ?? 1) * (guessing.told_actual ? 3 : 1) * tokenLimits.source_guessing!.standard;
    expect(within(row("source_guessing")).getByRole("cell", { name: `${new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 }).format(most)} tokens` })).toBeInTheDocument();
  });

  it("shows a limit the study carries for an eval that generates nothing, with the way back", () => {
    const template = studyOneTemplate();
    const { state } = mount(["token-limits"], { ...template, cards: template.cards.map((selection) => (selection.card_id === "target_hit_ranking" ? { ...selection, overrides: { max_tokens: 500 } } : selection)) });
    expect(within(row("target_hit_ranking")).getByText(/set in this study, but this eval generates nothing and takes no token limit/)).toBeInTheDocument();
    expect(within(row("target_hit_ranking")).queryByRole("spinbutton")).toBeNull();
    // The buttons for all evals leave it alone: it has no standard and no safe limit.
    fireEvent.click(screen.getByRole("button", { name: "Set all to safe" }));
    expect(limitOf(state.latest, "target_hit_ranking")).toBe(500);
    fireEvent.click(screen.getByRole("button", { name: "Set all to standard" }));
    fireEvent.click(within(row("target_hit_ranking")).getByRole("button", { name: "Use the card's value" }));
    expect(JSON.stringify(state.latest)).toBe(JSON.stringify(template));
    expect(row("target_hit_ranking")).toBeUndefined();
  });

  it("says so when no selected eval generates replies", () => {
    mount(["token-limits"], { ...legacy(), cards: [] });
    expect(screen.getByText("No selected eval generates replies of its own, so there is no token limit to set.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("button", { name: "Set all to safe" })).toBeNull();
  });
});

describe("the conditioning panel", () => {
  it("cautions that a raw quantum arm has no matched control, and only while raw is chosen", () => {
    const { state } = mount(["conditioning"]);
    const caution = /differs from the pseudo-random arms by the device’s byte statistics as well as by the source, and the harness has no matched control for that/;
    expect(screen.queryByText(caution)).toBeNull();
    fireEvent.click(screen.getByLabelText("Raw: the device's measured bytes"));
    expect(state.latest.conditioning?.mode).toBe("raw");
    expect(screen.getByText(caution)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Hashed: uniform bytes"));
    expect(screen.queryByText(caution)).toBeNull();
  });

  it("says before the study is submitted that more than half of a digest may not be served", () => {
    const { state } = mount(["conditioning"]);
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.change(screen.getByLabelText("Bytes kept from each digest"), { target: { value: "17" } });
    expect(state.latest.conditioning?.keep_bytes).toBe(17);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Serving more than half of each digest may compromise the integrity of the signal; keep at most 16 bytes of a SHA-256 digest.",
    );
    fireEvent.change(screen.getByLabelText("Bytes kept from each digest"), { target: { value: "16" } });
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("the design panels", () => {
  const DESIGN = ["claim", "outcomes", "alpha", "positive-control", "margins", "expectations"];

  it("set the level, the outcomes and the expectations", () => {
    const { state } = mount(DESIGN);
    fireEvent.change(screen.getByLabelText("False-finding rate (α)"), { target: { value: "0.01" } });
    expect(state.latest.design?.alpha).toBe(0.01);
    expect(group("Outcomes that decide the study (m = 3)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /^MEQ-30 total/ }));
    expect(state.latest.design?.confirmatory).toHaveLength(4);
    expect(group("Outcomes that decide the study (m = 4)")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("What do you expect to see?"), { target: { value: "No difference." } });
    expect(state.latest.expectations).toBe("No difference.");
    expect(parseStudyConfig(state.latest)).not.toBeNull();
  });

  it("offer the claim of an absence only once a positive control runs in every comparison", () => {
    const { state } = mount(["comparison", ...DESIGN]);
    const absence = () => screen.getByRole("radio", { name: /^A difference, or its absence within a margin/ });
    expect(absence()).toBeDisabled();
    expect(screen.getByLabelText("Positive control that must be detected")).toHaveValue("");

    fireEvent.click(screen.getByRole("button", { name: "ε 0.10: planned, off. Switch to on." }));
    expect(absence()).toBeEnabled();
    fireEvent.click(absence());
    expect(state.latest.design).toMatchObject({ claim: "difference_or_equivalence", sensitivity_epsilon: 0.1 });
    expect(screen.getByLabelText("Positive control that must be detected")).toHaveValue("0.1");
    expect(parseStudyConfig(state.latest)).not.toBeNull();
  });

  it("show a card's margin read-only and take a declared one where the outcome has a range", () => {
    const { state } = mount(DESIGN);
    const margins = within(group("Smallest difference that matters"));
    expect(margins.getByText(/±0\.05 · from the eval card/)).toBeInTheDocument();
    expect(margins.queryByRole("spinbutton")).toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: /^MEQ-30 total/ }));
    const declared = margins.getByRole("spinbutton", { name: "MEQ-30 total: smallest difference that matters (±)" });
    fireEvent.change(declared, { target: { value: "0.05" } });
    expect(state.latest.design?.margins).toEqual([{ card_id: "altered_state_meq", endpoint: "meq30_total", margin: 0.05 }]);
    fireEvent.change(declared, { target: { value: "" } });
    expect(state.latest.design?.margins).toEqual([]);
    // The same sentence is the basis line of each margin's chip; the note is the paragraph.
    expect(margins.getByText("Fixed now. Never derived from a pilot.", { selector: "p" })).toBeInTheDocument();
  });

  it("offer a legacy study a design, and nothing else, until it has one", () => {
    const { state } = mount(DESIGN, legacy());
    // That the study is on the built-in plan is said by the problem list of the page; the panel says what a
    // design is and what declaring one changes, once.
    expect(screen.queryByText(/This draft uses the fixed Study 1 plan\./)).toBeNull();
    expect(screen.getByText(/Declaring one makes it this study's analysis plan in place of the built-in one, so the study's hash changes\./)).toBeInTheDocument();
    expect(screen.queryByLabelText("False-finding rate (α)")).toBeNull();
    expect(screen.getAllByText("Set once a design is declared.")).toHaveLength(4);
    expect(screen.queryByRole("button", { name: "Remove the design block" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Declare a design" }));
    expect(state.latest.design).toMatchObject({ claim: "difference", alpha: 0.05, sensitivity_epsilon: null });
    expect(state.latest.design?.confirmatory).toHaveLength(3);
    expect(screen.getByLabelText("False-finding rate (α)")).toHaveValue("0.05");
    expect(screen.queryByRole("button", { name: "Declare a design" })).toBeNull();
    // Declaring adds the design block and touches nothing else: the probe keeps its own repetitions.
    expect(state.latest.cards).toEqual(legacy().cards);
    expect(state.latest.cards.find((selection) => selection.card_id === "self_referential_probe")?.overrides?.k ?? null).toBeNull();

    // And back: the design block goes, and the study is the file it was, byte for byte.
    const declared = state.latest;
    expect(screen.getByText("Return to the built-in plan").closest("details")).not.toHaveAttribute("open");
    fireEvent.click(screen.getByRole("button", { name: "Remove the design block" }));
    expect(state.latest.design ?? null).toBeNull();
    expect({ ...state.latest, design: declared.design }).toEqual(declared);
    expect(state.latest).toEqual(legacy());
    expect(toStudyYaml(state.latest)).toBe(toStudyYaml(legacy()));
    expect(studySnapshot(state.latest)).toBe(studySnapshot(studyOneJson));
    expect(screen.getByRole("button", { name: "Declare a design" })).toBeInTheDocument();
  });

  it("say what declaring a design changes and what it leaves, and that it can be undone", () => {
    mount(DESIGN, legacy());
    const note = screen.getByText(/Declaring one makes it this study's analysis plan/);
    expect(note).toHaveTextContent("It starts every choice of the design at its recommended value and changes nothing else: prompts, repetitions and arms stay as they are.");
    expect(note).toHaveTextContent("Removing the design block again, on this step, gives back the study as it was before it was declared.");
  });

  it("offer, after a design is declared, the repetitions an outcome that now decides the study needs, in the table of prompts and repetitions", () => {
    const { state } = mount([...DESIGN, "prompts"], legacy());
    fireEvent.click(screen.getByRole("button", { name: "Declare a design" }));
    const deciding = () => within(screen.getByRole("table", { name: "Prompts and repetitions of the outcomes that decide the study" }));
    const row = () => deciding().getByRole("rowheader", { name: /Experience-claim rate/ }).closest("tr")!;
    const k = () => within(row()).getByRole("button", { name: /^Repetitions \(k\): k = \d+\./ });
    const prompts = () => within(row()).getByRole("button", { name: /^Prompts: \d+ prompts\./ });
    // At the eval's own k = 20 its 30 prompts are close to enough: both chips say so, with one rating.
    expect(k()).toHaveClass("rec--marginal");
    expect(prompts()).toHaveClass("rec--marginal");
    expect(k()).toHaveAccessibleName(/With k = 24, the 30 prompts of this eval are enough for its margin\./);
    // The eval has no more prompts to give, so the only button in the row is the one on the repetitions.
    fireEvent.click(within(row()).getByRole("button", { name: "Use recommended" }));
    expect(state.latest.cards.find((selection) => selection.card_id === "self_referential_probe")?.overrides?.k).toBe(24);
    expect(k()).toHaveClass("rec--recommended");
    expect(k()).toHaveAccessibleName(/^Repetitions \(k\): k = 24\./);
    expect(prompts()).toHaveClass("rec--sufficient");
    expect(within(row()).queryByRole("button", { name: "Use recommended" })).toBeNull();
  });

  it("offers as the positive control only levels whose arm runs in every comparison, and keeps showing the one the study names", () => {
    // The template plans three biased arms and runs none of them.
    const { state } = mount(DESIGN);
    const select = () => screen.getByLabelText("Positive control that must be detected") as HTMLSelectElement;
    expect([...select().options].map((option) => option.textContent)).toEqual(["None"]);
    cleanup();
    // A study that names a level whose arm is switched off still shows it, for what it is.
    const off = mount(DESIGN, { ...studyOneTemplate(), design: { ...studyOneTemplate().design!, sensitivity_epsilon: 0.03 } });
    expect([...select().options].map((option) => option.textContent)).toEqual(["None", "ε 0.03 (switched off)"]);
    fireEvent.change(select(), { target: { value: "" } });
    expect(off.state.latest.design?.sensitivity_epsilon ?? null).toBeNull();
    expect(state.edits).toBe(0);
  });

  it("states both limits of a declared margin", () => {
    const meq = { card_id: "altered_state_meq", endpoint: "meq30_total" };
    mount(DESIGN, toggleConfirmatory(studyOneTemplate(), meq));
    const field = screen.getByLabelText("MEQ-30 total: smallest difference that matters (±)");
    expect(field).toHaveAttribute("min", "0.001");
    expect(field).toHaveAttribute("max", "0.1");
    expect(screen.getByText("Between 0.001 and 0.1: a thousandth to a tenth of the outcome's range. Leave empty for no margin.")).toBeInTheDocument();
  });
});

describe("the judge panel", () => {
  it("edits the judge, and says when no selected eval uses it", () => {
    const { state } = mount(["judge"]);
    fireEvent.change(screen.getByLabelText("Judge model"), { target: { value: "claude-sonnet-5-5" } });
    fireEvent.change(screen.getByLabelText("Longest answer, in tokens"), { target: { value: "2048" } });
    fireEvent.change(screen.getByLabelText("Extended thinking"), { target: { value: "model_default" } });
    fireEvent.change(screen.getByLabelText("Temperature"), { target: { value: "set" } });
    fireEvent.change(screen.getByLabelText("Temperature value (0 to 1)"), { target: { value: "0.3" } });
    expect(state.latest.judge).toEqual({ provider: "anthropic", model: "claude-sonnet-5-5", temperature: 0.3, max_tokens: 2048, thinking: "model_default", rubric_hash: null });
    expect(screen.queryByText(/None of the selected evals uses the judge/)).toBeNull();
  });

  it("lets a model id be typed that begins with a listed one", () => {
    const { state } = mount(["judge"]);
    fireEvent.change(screen.getByLabelText("Judge model"), { target: { value: "__custom__" } });
    // Typed one character at a time, as at a keyboard: at the sixteenth the text equals a listed id.
    const typed = "claude-haiku-5-5-20261001";
    for (let length = 1; length <= typed.length; length += 1) {
      fireEvent.change(screen.getByLabelText("Exact model id"), { target: { value: typed.slice(0, length) } });
    }
    expect(state.latest.judge.model).toBe(typed);
    expect(screen.getByLabelText("Exact model id")).toHaveValue(typed);
    expect(screen.getByLabelText("Judge model")).toHaveValue("__custom__");
    // Choosing a listed model closes the box again.
    fireEvent.change(screen.getByLabelText("Judge model"), { target: { value: "claude-sonnet-5-5" } });
    expect(screen.queryByLabelText("Exact model id")).toBeNull();
    expect(state.latest.judge.model).toBe("claude-sonnet-5-5");
  });

  it("takes any exact model id", () => {
    const { state } = mount(["judge"]);
    fireEvent.change(screen.getByLabelText("Judge model"), { target: { value: "__custom__" } });
    expect(state.latest.judge.model).toBe("");
    fireEvent.change(screen.getByLabelText("Exact model id"), { target: { value: "claude-other-1" } });
    expect(state.latest.judge.model).toBe("claude-other-1");
  });

  it("notes that the judge is unused when only token-level evals are selected", () => {
    mount(["judge"], { ...legacy(), cards: [{ card_id: "tier0_pit_residuals" }] });
    expect(screen.getByText(/None of the selected evals uses the judge/)).toBeInTheDocument();
  });
});
