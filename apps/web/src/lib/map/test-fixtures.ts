import { cards, type EvalCard } from "@qr-evals/contracts";
import fixture from "../../../../../packages/contracts/fixtures/design.json";
import type { WizardStudyConfig } from "../config-store";
import { problems, type Problem } from "../problems";
import { rate, type Rec } from "../recommendations";
import { buildStudyGraph, type StudyGraph } from "./graph";

// Shared by the map's tests: the studies of `packages/contracts/fixtures/design.json`, each as the page would
// hold it, with the problem list and the ratings the page would compute for it.

export interface BackendProblem { code: string; severity: string; message: string; step_id: string; node_id: string }

export interface FixtureCase {
  name: string;
  config: WizardStudyConfig;
  /** The problems the backend reports for the study. */
  backendProblems: BackendProblem[];
}

interface RawCase { name: string; config: Record<string, unknown>; problems: BackendProblem[] }

export const FIXTURE_CASES: readonly FixtureCase[] = (fixture.cases as unknown as RawCase[]).map((item) => ({
  name: item.name,
  // The fixture holds a study as the backend dumps it, which has no title.
  config: { ...item.config, title: item.name } as unknown as WizardStudyConfig,
  backendProblems: item.problems,
}));

export function fixtureCase(name: string): FixtureCase {
  const found = FIXTURE_CASES.find((item) => item.name === name);
  if (!found) throw new Error(`design.json has no case ${name}`);
  return found;
}

export interface Built { graph: StudyGraph; problems: Problem[]; recs: Rec[] }

/** The graph of a study, from the lists the setup pages compute. */
export function build(config: WizardStudyConfig, registry: readonly EvalCard[] = cards): Built {
  const all = problems(config, registry);
  const recs = rate(config, registry, all);
  return { graph: buildStudyGraph(config, registry, all, recs), problems: all, recs };
}

/** Every node that unfolding can open. */
export function expandableIds(graph: StudyGraph): string[] {
  return graph.nodes.filter((node) => node.expandable).map((node) => node.id);
}
