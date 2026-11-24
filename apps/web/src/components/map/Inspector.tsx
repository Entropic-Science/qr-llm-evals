import { useEffect, useRef, type ReactNode } from "react";
import { STATIONS, TOKEN_AUDITS, type MapNode, type StationId, type StudyGraph } from "../../lib/map/graph";
import type { MapNote } from "../../lib/map/notes";
import type { Problem, StepId } from "../../lib/problems";
import { recFor, type Rec, type RecState } from "../../lib/recommendations";
import { tokenLimitFor } from "../../lib/token-limits";
import {
  ArmTable, CellGridField, ControlSeedsField, CustomArmsNotice, DeterministicField, EpsilonArmsField, QuantumArmField,
  SeedValuesField, SupervisedToggleField, rosterOf,
} from "../../routes/configure/fields/compare";
import {
  AlphaField, ClaimField, DeclareDesignField, ExpectationsField, MarginField, OutcomesField, SensitivityEpsilonField,
} from "../../routes/configure/fields/design";
import { FieldScope, FieldState, type FieldProps } from "../../routes/configure/fields/Field";
import {
  DisplaySettingsField, GroupSessionField, IntentionTimerField, JudgeMaxTokensField, JudgeModelField, JudgeProviderField,
  JudgeTemperatureField, JudgeThinkingField, NarrativeModelField, RefocusField, SessionDisplayField,
} from "../../routes/configure/fields/instruments";
import { CardKField, CardPromptsField, EvalChecklistField } from "../../routes/configure/fields/measure";
import { AuthorsField, ContactField, TitleField } from "../../routes/configure/fields/study";
import { CardTokenLimitField, CardTokenPresets } from "../../routes/configure/fields/token-limits";
import { locateField } from "../../routes/configure/focus";
import { useStudy } from "../../routes/configure/StudyContext";
import { HypothesisCard, VerdictStamps } from "../study/design";
import { MapChip, StatePip } from "./MapChip";
import { editable } from "./model";
import { PromptDeck } from "./Prompts";

// What a popover of the study map holds: the settings of a node or of a marginal note, drawn with the very
// field components the setup steps and the settings dialog use, so an edit made here is the same edit; the
// problems that sit on it, each with its one-click fix; and for an eval, its prompts.

/** The prefix of the element ids of the fields in a map popover: the same field may be on the page in a step too. */
export const MAP_SCOPE = "map-";

/** What a popover is about: a node of the graph, or a marginal note. */
export interface Subject {
  id: string;
  /** The node, or for a note the node it hangs on (none for a note of a station). */
  node: MapNode | null;
  note: MapNote | null;
  /** The node's kind, or `note-{kind}`: the class of the popover. */
  kind: string;
  station: StationId;
  label: string;
  sub: string;
  story: string;
  state: RecState | null;
  recs: readonly Rec[];
  problems: readonly Problem[];
  /** The study key paths and `Rec` paths edited here. */
  settings: readonly string[];
  stepId: StepId;
  describes: string;
}

export function subjectOfNode(node: MapNode, state: RecState | null = node.state, problems: readonly Problem[] = node.problems): Subject {
  return {
    id: node.id, node, note: null, kind: node.kind, station: node.station, label: node.label, sub: node.sub, story: node.facts.story,
    state, recs: node.recs, problems, settings: node.settings, stepId: node.stepId, describes: node.describes,
  };
}

export function subjectOfNote(note: MapNote, graph: Pick<StudyGraph, "nodes">): Subject {
  const anchor = note.anchor;
  const node = anchor.type === "node" ? graph.nodes.find((item) => item.id === anchor.node) ?? null : null;
  const where = node !== null ? `It is noted beside ${node.label}.` : `It is noted at the ${STATIONS.find((station) => station.id === note.station)?.label ?? note.station} station.`;
  return {
    id: note.id, node, note, kind: `note-${note.kind}`, station: note.station, label: note.label, sub: note.text, story: note.story,
    state: note.state, recs: note.recs, problems: note.problems, settings: note.settings, stepId: note.stepId, describes: where,
  };
}

/** The first setting of a subject that the settings dialog can open, or `null`. */
export function settingOfSubject(subject: Pick<Subject, "settings">): string | null {
  return subject.settings.find(editable) ?? null;
}

/** Prompts and repetitions of one eval, each with its label and its rating: the two cells of the prompts table, on their own. */
function CardSize({ cardId, props }: { cardId: string; props: FieldProps }) {
  const card = props.cards.find((candidate) => candidate.id === cardId);
  const selection = props.config.cards.find((candidate) => candidate.card_id === cardId);
  if (card === undefined || selection === undefined) return null;
  return <div className="map-fields__pair">
    <div className="field map-field" data-path={`cards.${cardId}.items`}>
      <span className="map-field__label">Prompts (N)</span>
      <CardPromptsField {...props} cardId={cardId} />
      <FieldState rec={recFor(props.recs, `cards.${cardId}.items`)} {...props} />
    </div>
    <div className="field map-field" data-path={`cards.${cardId}.k`}>
      <span className="map-field__label">Repetitions (k)</span>
      <CardKField {...props} cardId={cardId} />
      <FieldState rec={recFor(props.recs, `cards.${cardId}.k`)} {...props} />
    </div>
    <p className="field-note">Prompts are the unit of analysis. Repetitions sharpen each prompt's value, so fewer repetitions call for more prompts; they are never counted as prompts themselves.</p>
    {tokenLimitFor(cardId) !== undefined || (selection.overrides?.max_tokens ?? null) !== null ? <div className="field map-field" data-path={`cards.${cardId}.max_tokens`}>
      <span className="map-field__label">Token limit</span>
      <CardTokenLimitField {...props} cardId={cardId} />
      <CardTokenPresets {...props} cardId={cardId} />
    </div> : null}
  </div>;
}

/** The fields of a marginal note. */
function noteFields(note: MapNote, props: FieldProps): ReactNode {
  switch (note.kind) {
    case "alpha": return <AlphaField {...props} />;
    case "narrative": return <NarrativeModelField {...props} />;
    case "people": return <><AuthorsField {...props} /><ContactField {...props} /></>;
    case "sessions": {
      const display = props.config.supervised?.display ?? "single";
      return <>
        <SessionDisplayField {...props} />
        <IntentionTimerField {...props} />
        <RefocusField {...props} />
        <GroupSessionField {...props} />
        <DisplaySettingsField {...props} mode={display} />
      </>;
    }
    case "judge": return <>
      <JudgeProviderField />
      <JudgeModelField {...props} />
      <JudgeMaxTokensField {...props} />
      <JudgeThinkingField {...props} />
      <JudgeTemperatureField {...props} />
    </>;
    // Power, completeness and the sampling envelope are the harness's; they are stated, not set.
    default: return null;
  }
}

/** The fields of a node: the same components, with the same edits, as the step that holds its settings. */
function nodeFields(node: MapNode, props: FieldProps): ReactNode {
  const legacy = props.config.design === null || props.config.design === undefined;
  const custom = rosterOf(props) === null;
  const arms = (fields: ReactNode) => (custom ? <CustomArmsNotice {...props} /> : fields);
  const designed = (fields: ReactNode) => (legacy ? <DeclareDesignField {...props} /> : fields);
  const ref = node.ref;
  switch (ref.type) {
    case "study": return <><TitleField {...props} /><DeclareDesignField {...props} /><ClaimField {...props} /><ExpectationsField {...props} /></>;
    case "arm":
      if (node.kind === "quantum_arm") return arms(<><CellGridField {...props} /><QuantumArmField {...props} /><DeterministicField {...props} /></>);
      if (node.kind === "seeded_arm") return arms(<SeedValuesField {...props} />);
      if (node.kind === "positive_control") return arms(<><EpsilonArmsField {...props} />{legacy ? null : <SensitivityEpsilonField {...props} />}</>);
      if (node.kind === "supervised_arm") return arms(<SupervisedToggleField {...props} />);
      return custom ? <CustomArmsNotice {...props} /> : <ArmTable config={props.config} />;
    case "deck": return arms(<><ControlSeedsField {...props} /><SeedValuesField {...props} /></>);
    case "tier0":
    case "group": return <EvalChecklistField {...props} />;
    case "card": return ref.known && ref.selected ? <CardSize cardId={ref.cardId} props={props} /> : <EvalChecklistField {...props} />;
    case "endpoint": return designed(<><OutcomesField {...props} />{ref.role === "confirmatory" ? <MarginField {...props} cardId={ref.cardId} endpoint={ref.endpoint} /> : null}</>);
    case "test": return <CardSize cardId={ref.hypothesis.card_id} props={props} />;
    case "family": return designed(<><AlphaField {...props} /><OutcomesField {...props} /></>);
    case "gate":
      if (ref.gate === "positive") return designed(<><SensitivityEpsilonField {...props} />{custom ? null : <EpsilonArmsField {...props} />}</>);
      if (ref.gate === "negative") return arms(<ControlSeedsField {...props} />);
      return null;
    case "verdict": return <><ClaimField {...props} /><SensitivityEpsilonField {...props} /></>;
    case "ghost":
      if (ref.invites === "arms") return arms(<CellGridField {...props} />);
      if (ref.invites === "evals") return <EvalChecklistField {...props} />;
      return designed(<OutcomesField {...props} />);
    default: return null;
  }
}

/** The decision rules are printed as the pre-registration states them; this says what their symbols stand for. */
export const RULES_KEY = "pD is the p-value of the test for a difference and pE that of the test for equivalence within the margin, both after Holm's correction; alpha is the false-finding rate of the study.";

const SEVERITY: Record<Problem["severity"], { text: string; glyph: string }> = {
  error: { text: "Blocks launch", glyph: "✕" },
  warning: { text: "Warning", glyph: "!" },
};

export interface InspectorProps {
  graph: StudyGraph;
  subject: Subject;
  /** The setting to put the focus on (the chip that opened the popover), and a count that tells two requests apart. */
  focus?: { path: string; nonce: number } | null;
  /** What the step that holds these settings is called. */
  stepLabel: string;
  /** Opens the settings dialog on a setting. */
  onSetting: (path: string) => void;
  onProblem: (problem: Problem) => void;
  onFix: (problem: Problem) => void;
  onStep: () => void;
  /** Opens another node of the map in place of this one. */
  onOpen: (id: string) => void;
  /** More about the node, drawn at the end by whoever mounts the map. */
  children?: ReactNode;
}

/**
 * Everything a popover says and edits about its subject. It is the body and the foot of the popover on the
 * diagram, and the same under an entry of the outline.
 */
export function Inspector({ graph, subject, focus, stepLabel, onSetting, onProblem, onFix, onStep, onOpen, children }: InspectorProps) {
  const { panelProps, config } = useStudy();
  const body = useRef<HTMLDivElement>(null);
  const { node, note } = subject;
  const own = note === null ? node : null;
  const fields = note !== null ? noteFields(note, panelProps) : own !== null ? nodeFields(own, panelProps) : null;
  // What has a field above is not listed again; what the harness fixes is listed with its reason.
  const stated = subject.recs.filter((rec) => rec.state === "locked" || !editable(rec.path));
  const held = own === null ? [] : own.children.flatMap((id) => graph.nodes.find((child) => child.id === id) ?? []);
  const setting = settingOfSubject(subject);
  const claim = config.design?.claim ?? "difference";
  const testedCard = own?.ref.type === "test" ? own.ref.hypothesis.card_id : null;
  const evalNode = testedCard === null ? undefined : graph.nodes.find((item) => item.id === `card:${testedCard}`);
  const about = own === null ? null : <>
    <ul className="map-sheet__facts">
      {(own.ref.type === "test" || own.ref.type === "verdict" ? own.facts.detail.slice(-1) : [...own.facts.design, ...own.facts.detail]).map((fact) => <li key={fact}>{fact}</li>)}
    </ul>
    {held.length > 0 ? <div className="map-sheet__held">
      <h4>{own.kind === "card" ? "Outcomes" : "Holds"}</h4>
      <ul>
        {held.map((child) => <li key={child.id}>
          {child.state !== null ? <StatePip state={child.state} /> : null}
          <span><strong>{child.label}</strong> <small>{child.sub}</small></span>
        </li>)}
      </ul>
    </div> : null}
    <p className="map-sheet__describes">{own.describes}</p>
  </>;

  // A popover opened from a chip starts on that chip's field.
  const wanted = focus?.path;
  const nonce = focus?.nonce;
  useEffect(() => {
    const root = body.current;
    if (wanted === undefined || !root) return;
    const field = locateField(root, wanted);
    if (!field) return;
    for (let parent = field.parentElement; parent && parent !== root; parent = parent.parentElement) {
      if (parent instanceof HTMLDetailsElement) parent.open = true;
    }
    const control = field.querySelector<HTMLElement>("input:not([disabled]), select:not([disabled]), textarea:not([disabled])") ?? field.querySelector<HTMLElement>("button:not([disabled])");
    if (!control) return;
    control.focus({ preventScroll: true });
    // Only the popover's own body scrolls: the map does not move under it.
    const top = control.getBoundingClientRect().top - root.getBoundingClientRect().top;
    if (top < 0 || top > root.clientHeight - 40) root.scrollTop += top - 24;
  }, [wanted, nonce, subject.id]);

  return <>
    <div className="map-sheet__body" ref={body}>
      {/* A test's sentence is its hypothesis, which its card states. */}
      {own?.ref.type !== "test" ? <p className="map-sheet__story">{subject.story}</p> : null}
      {subject.problems.length > 0 ? <ul className="map-sheet__problems" aria-label={`Problems on ${subject.label}`}>
        {subject.problems.map((problem) => <li key={`${problem.code}|${problem.nodeId}|${problem.message}`} className={`map-sheet__problem map-sheet__problem--${problem.severity}`} data-code={problem.code}>
          <strong><span aria-hidden="true">{SEVERITY[problem.severity].glyph}</span> {SEVERITY[problem.severity].text}</strong>
          <span>{problem.message}</span>
          <span className="map-sheet__actions">
            {problem.fix ? <button type="button" className="text-button" onClick={() => onFix(problem)}>{problem.fix.label}</button> : null}
            <button type="button" className="text-button" onClick={() => onProblem(problem)}>Show in all settings</button>
          </span>
        </li>)}
      </ul> : null}
      {own?.ref.type === "test" ? <HypothesisCard hypothesis={own.ref.hypothesis} claim={claim} headingLevel={4} /> : null}
      {own?.ref.type === "verdict" ? <>
        <VerdictStamps subject={own.ref.hypothesis.id} stamps={own.ref.stamps} />
        <dl className="map-sheet__rules">
          {own.ref.stamps.map((stamp) => <div key={stamp.verdict}>
            <dt>{stamp.label}</dt>
            <dd>{stamp.rules.join(" Or: ")}</dd>
          </div>)}
        </dl>
        <p className="map-sheet__key">{RULES_KEY}</p>
      </> : null}
      {fields !== null ? <FieldScope.Provider value={MAP_SCOPE}>
        <div className="map-fields" role="group" aria-label={`Settings of ${subject.label}`}>{fields}</div>
      </FieldScope.Provider> : null}
      {stated.length > 0 ? <dl className="map-sheet__settings" aria-label={`Set by the harness on ${subject.label}`}>
        {stated.map((rec) => <div key={rec.path}>
          <dt>{rec.label}</dt>
          <dd>
            <MapChip rec={rec} />
            <span className="map-sheet__evidence">{rec.have} {rec.basis}</span>
          </dd>
        </div>)}
      </dl> : null}
      {own?.ref.type === "card" ? <PromptDeck graph={graph} node={own} onOpen={onOpen} /> : null}
      {own?.ref.type === "tier0" ? <p className="prompt-deck__state prompt-deck__state--no_prompts">{TOKEN_AUDITS}</p> : null}
      {evalNode !== undefined ? <p className="map-sheet__link"><button type="button" className="text-button" onClick={() => onOpen(evalNode.id)}>Read the prompts of {evalNode.label}</button></p> : null}
      {/* An eval's popover is for its prompts; what else the map knows about it is one press away. */}
      {own === null ? <p className="map-sheet__describes">{subject.describes}</p>
        : own.kind === "card" ? <details className="map-sheet__more"><summary>About this eval</summary>{about}</details>
          : about}
      {children}
    </div>
    <footer className="map-sheet__foot">
      {setting !== null ? <button type="button" className="button button--secondary" onClick={() => onSetting(setting)}>Open in all settings</button> : null}
      <button type="button" className="text-button" onClick={onStep}>Go to the {stepLabel} step</button>
    </footer>
  </>;
}
