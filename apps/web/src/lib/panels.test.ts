import schema from "../../../../packages/contracts/schema/StudyConfig.json";
import { PANELS, SECTIONS, panelFor, panelsIn, type SectionId } from "./panels";

type Node = Record<string, unknown>;

/**
 * Every leaf key path of a JSON schema: list items as `[]`, the keys of an open map as `.*`, `$ref`s followed,
 * "or null" unwrapped. A leaf is anything that is not an object with named or open keys and not a list.
 */
function leafPaths(root: Node): string[] {
  const definitions = (root.$defs ?? {}) as Record<string, Node>;
  const resolve = (node: Node): Node => {
    if (typeof node.$ref === "string") {
      const name = node.$ref.replace("#/$defs/", "");
      const target = definitions[name];
      if (!target) throw new Error(`the schema has no definition ${name}`);
      return resolve(target);
    }
    if (Array.isArray(node.anyOf)) {
      const options = (node.anyOf as Node[]).filter((option) => option.type !== "null");
      if (options.length === 1) return resolve(options[0]!);
    }
    return node;
  };
  const out: string[] = [];
  const walk = (raw: Node, path: string) => {
    const node = resolve(raw);
    if (node.type === "array" && node.items) { walk(node.items as Node, `${path}[]`); return; }
    const properties = node.properties as Record<string, Node> | undefined;
    const open = node.additionalProperties;
    if (properties || (typeof open === "object" && open !== null)) {
      for (const [key, child] of Object.entries(properties ?? {})) walk(child, path ? `${path}.${key}` : key);
      if (typeof open === "object" && open !== null) walk(open as Node, `${path}.*`);
      return;
    }
    out.push(path);
  };
  walk(root, "");
  return out;
}

const leaves = leafPaths(schema as unknown as Node);
const claims = PANELS.flatMap((panel) => panel.paths.map((path) => ({ path, panel: panel.id })));
const within = (path: string, parent: string) => path.startsWith(`${parent}.`) || path.startsWith(`${parent}[`);

describe("the schema walk", () => {
  it("finds the leaves of StudyConfig, nested and listed", () => {
    expect(leaves.length).toBeGreaterThan(60);
    expect(new Set(leaves).size).toBe(leaves.length);
    for (const path of [
      "title", "authors[]", "seeds[]", "template", "models[].checkpoint", "arms[].study_seed", "arms[].unavailable_reason",
      "cards[].card_id", "cards[].overrides.items", "cards[].overrides.max_tokens", "design.alpha", "design.confirmatory[].endpoint", "design.margins[].margin",
      "judge.rubric_hash", "narrative_model.served_name", "supervised.settings_by_display.tiled.show_thinking",
      "supervised.questionnaires_hash", "estimates.tps_by_model.*", "pilot.prompts", "data_licence",
    ]) expect(leaves).toContain(path);
    // Blocks and lists are not leaves.
    for (const path of ["arms", "arms[]", "design", "supervised", "supervised.settings_by_display.single", "estimates.tps_by_model"]) expect(leaves).not.toContain(path);
  });
});

describe("PANELS", () => {
  it("claims every key of a study in exactly one panel", () => {
    const unclaimed = leaves.filter((leaf) => !claims.some((claim) => claim.path === leaf));
    expect(unclaimed, "study keys no panel edits").toEqual([]);
    const twice = leaves.filter((leaf) => claims.filter((claim) => claim.path === leaf).length > 1);
    expect(twice, "study keys two panels edit").toEqual([]);
  });

  it("names nothing the schema does not have", () => {
    for (const { path, panel } of claims) {
      const known = leaves.includes(path) || leaves.some((leaf) => within(leaf, path));
      expect(known, `${panel} names ${path}, which is not a key of StudyConfig`).toBe(true);
    }
    // A block named whole is named once.
    const blocks = claims.filter(({ path }) => !leaves.includes(path)).map(({ path }) => path);
    expect(new Set(blocks).size).toBe(blocks.length);
  });

  it("has unique ids, known sections and the order of the settings page", () => {
    const ids = PANELS.map((panel) => panel.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) expect(id).toMatch(/^[a-z][a-z0-9-]*$/);
    const order = SECTIONS.map((section) => section.id);
    expect(order).toEqual(["study", "arms", "measures", "design", "supervised", "judge", "advanced"]);
    expect(SECTIONS.map((section) => section.label)).toEqual([
      "Study", "Models & arms", "Evals & repetitions", "Design & analysis", "Supervised sessions", "Judge & narrative", "Advanced",
    ]);
    const positions = PANELS.map((panel) => order.indexOf(panel.section));
    expect(positions.every((position) => position >= 0)).toBe(true);
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
    for (const section of order) expect(panelsIn(section as SectionId).length, section).toBeGreaterThan(0);
    expect(panelsIn("measures").map((panel) => panel.id)).toEqual(["evals", "prompts", "token-limits"]);
    expect(PANELS.find((panel) => panel.id === "token-limits")).toMatchObject({ label: "Token limits", paths: ["cards[].overrides.max_tokens"] });
    for (const panel of PANELS) {
      expect(panel.label.length).toBeGreaterThan(0);
      expect(typeof panel.Component).toBe("function");
      expect(panel.paths.length).toBeGreaterThan(0);
    }
  });

  it("keeps the settings that are not for a wizard step in advanced panels, and only those", () => {
    const advanced = (path: string) => claims.filter((claim) => claim.path === path).every((claim) => PANELS.find((panel) => panel.id === claim.panel)!.advanced === true);
    const hidden = leaves.filter((leaf) => (
      leaf.startsWith("estimates.") || leaf.startsWith("narrative_model.") || leaf.startsWith("supervised.settings_by_display.")
      || ["data_licence", "created_at", "template", "schema_version", "judge.rubric_hash", "supervised.target_pool_hash", "supervised.questionnaires_hash"].includes(leaf)
    ));
    expect(hidden.length).toBeGreaterThan(20);
    for (const leaf of hidden) expect(advanced(leaf), leaf).toBe(true);
    for (const leaf of [
      "title", "authors[]", "contact", "expectations", "cards[].card_id", "cards[].overrides.k", "cards[].overrides.items",
      "cards[].overrides.max_tokens", "design.claim", "design.alpha", "design.sensitivity_epsilon", "design.confirmatory[].card_id", "design.margins[].margin",
      "judge.model", "judge.max_tokens", "judge.thinking", "judge.temperature", "supervised.display", "supervised.group",
    ]) expect(advanced(leaf), leaf).toBe(false);
    expect(PANELS.filter((panel) => panel.section === "advanced").every((panel) => panel.advanced === true)).toBe(true);
  });

  it("has an arm table in the advanced section that edits every field of an arm", () => {
    const fields = Object.keys((schema.$defs.Arm as { properties: Record<string, unknown> }).properties);
    expect(fields).toHaveLength(10);
    const table = PANELS.find((panel) => panel.id === "arm-table")!;
    expect(table).toMatchObject({ section: "advanced", advanced: true });
    expect([...table.paths].sort()).toEqual(fields.map((field) => `arms[].${field}`).sort());
    // The roster view rewrites the list whole; it does not claim the fields.
    expect(PANELS.find((panel) => panel.id === "comparison")!.paths).toContain("arms");
  });
});

describe("panelFor", () => {
  const id = (path: string) => panelFor(path)?.id;

  it("finds the panel of a study key, of one list item and of a block", () => {
    expect(id("title")).toBe("title");
    expect(id("arms[].study_seed")).toBe("arm-table");
    expect(id("arms[3].study_seed")).toBe("arm-table");
    expect(id("arms")).toBe("comparison");
    expect(id("design")).toBe("claim");
    expect(id("design.margins")).toBe("margins");
    expect(id("design.confirmatory")).toBe("outcomes");
    expect(id("supervised.settings_by_display.single")).toBe("display-settings");
    expect(id("narrative_model")).toBe("narrative");
    expect(id("estimates.tps_by_model.qr-llm")).toBe("estimates");
  });

  it("finds the panel of a recommendation", () => {
    expect(id("cards.self_referential_probe.items")).toBe("prompts");
    expect(id("cards.self_referential_probe.k")).toBe("prompts");
    expect(id("cards.self_referential_probe.max_tokens")).toBe("token-limits");
    expect(id("cards[].overrides.max_tokens")).toBe("token-limits");
    expect(id("roster.seeds")).toBe("comparison");
    expect(id("design.alpha")).toBe("alpha");
    expect(id("design.sensitivity_epsilon")).toBe("positive-control");
    expect(id("design.margins.self_referential_probe/experience_claim_rate")).toBe("margins");
  });

  it("has no panel for what the harness fixes", () => {
    expect(panelFor("fixed.power")).toBeUndefined();
    expect(panelFor("no.such.key")).toBeUndefined();
  });

  it("finds a panel for every leaf", () => {
    for (const leaf of leaves) expect(panelFor(leaf), leaf).toBeDefined();
  });
});
