import type { CardItem } from "@qr-evals/contracts";
import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { MapNode, StudyGraph } from "../../lib/map/graph";
import {
  DEFAULT_TOLD, PROMPT_ROW_H, TOLD_CELLS, answersText, cutLength, firstLine, inStudy, promptLineage, roleLabel, rowWindow,
  sharedLead, sourceSentence, turnsOf, type PromptLineage, type Told,
} from "../../lib/map/prompts";
import { promptFailure, useCardItems } from "./usePrompts";

// The prompts of an eval, down to the single one: a list of them, the full text of the one that is picked,
// and what becomes of it. Drawn inside the popover of an eval on the map and in the outline.

const count = (value: number) => value.toLocaleString("en-US");
const listed = (names: readonly string[]): string => (names.length <= 1 ? names[0] ?? "" : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`);

/** How many rows the list shows when its height is not known, and how close to the end the next page is asked for. */
const VISIBLE_ROWS = 6;
const NEAR_END = 10;

interface PromptListProps {
  /** What the list is called: "Prompts of {eval}". */
  label: string;
  items: readonly CardItem[];
  /** Every prompt the eval has, loaded or not. */
  total: number;
  /** How many of them the study runs: the first `chosen`, in the eval's own order. */
  chosen: number;
  selected: number | null;
  onSelect: (index: number) => void;
  /** Whether more can be read, and the way to read them. */
  more: boolean;
  loadingMore: boolean;
  /** Whether the last request for more failed. The list then waits to be asked, and does not ask again by itself. */
  moreFailed?: boolean;
  onMore: () => void;
}

/**
 * The prompts of an eval as a list: each with its place, its id and its first line, and whether the study
 * runs it. Only the rows near the ones in view are mounted, and the next page is read when the end comes near.
 */
export function PromptList({ label, items, total, chosen, selected, onSelect, more, loadingMore, moreFailed = false, onMore }: PromptListProps) {
  const base = useId();
  const list = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const { first, last } = rowWindow(scrollTop, items.length);
  const rows = items.slice(first, last);
  // A preamble every prompt opens with tells none of them apart: the rows start after it.
  const lead = useMemo(() => sharedLead(items), [items]);
  // The last row in view. Where the list has not been measured (no layout), a few rows stand in for its height.
  const inView = Math.floor(scrollTop / PROMPT_ROW_H) + Math.ceil((list.current?.clientHeight || VISIBLE_ROWS * PROMPT_ROW_H) / PROMPT_ROW_H);
  const nearEnd = inView >= items.length - NEAR_END;

  // The end of what is loaded is in reach: read the next page. A page that failed is not asked for again from
  // here, or the list would ask for as long as it stays scrolled to its end; the buttons ask again.
  useEffect(() => {
    if (nearEnd && more && !loadingMore && !moreFailed) onMore();
  }, [nearEnd, more, loadingMore, moreFailed, onMore]);

  const at = selected === null ? -1 : items.findIndex((item) => item.index === selected);
  const move = (to: number) => {
    const item = items[Math.max(0, Math.min(items.length - 1, to))];
    if (item === undefined) return;
    onSelect(item.index);
    // Keep the picked row in view without asking the page to scroll anything else.
    const element = list.current;
    if (!element) return;
    const top = items.indexOf(item) * PROMPT_ROW_H;
    const height = element.clientHeight || VISIBLE_ROWS * PROMPT_ROW_H;
    if (top < element.scrollTop) element.scrollTop = top;
    else if (top + PROMPT_ROW_H > element.scrollTop + height) element.scrollTop = top + PROMPT_ROW_H - height;
    setScrollTop(element.scrollTop);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const steps: Record<string, number> = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: items.length - 1, PageDown: at + 6, PageUp: at - 6 };
    const to = steps[event.key];
    if (to === undefined) return;
    event.preventDefault();
    event.stopPropagation();
    move(to);
  };

  return <div className="prompt-list-frame">
    <div
      className="prompt-list"
      role="listbox"
      tabIndex={0}
      ref={list}
      aria-label={label}
      aria-activedescendant={at >= 0 ? `${base}-${selected}` : undefined}
      onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
      onKeyDown={onKeyDown}
    >
      <div className="prompt-list__rows" style={{ height: items.length * PROMPT_ROW_H }}>
        {rows.map((item, offset) => {
          const runs = inStudy(item.index, chosen);
          const classes = ["prompt-row", runs ? "is-in" : "is-beyond", item.index === chosen ? "is-first-beyond" : "", item.index === selected ? "is-selected" : ""].filter(Boolean).join(" ");
          return <div
            key={item.index}
            id={`${base}-${item.index}`}
            className={classes}
            role="option"
            aria-selected={item.index === selected}
            data-index={item.index}
            data-in-study={runs}
            style={{ top: (first + offset) * PROMPT_ROW_H, height: PROMPT_ROW_H }}
            onClick={() => onSelect(item.index)}
          >
            <span className="prompt-row__index">{item.index + 1}</span>
            <span className="prompt-row__id">{item.prompt_id}</span>
            <span className="prompt-row__line">{firstLine(item, lead)}</span>
            {runs ? null : <span className="prompt-row__out">not in this study</span>}
          </div>;
        })}
      </div>
    </div>
    <p className="prompt-list__count">
      {items.length < total ? `${count(items.length)} of ${count(total)} prompts are listed so far.` : `All ${count(total)} prompts are listed.`}
      {more ? <> <button type="button" className="text-button" disabled={loadingMore} onClick={onMore}>{loadingMore ? "Reading more…" : moreFailed ? "Try again" : "Read more"}</button></> : null}
    </p>
  </div>;
}

interface PromptStripProps {
  lineage: PromptLineage;
  /** Whether the study runs the prompt. */
  runs: boolean;
  chosen: number;
  /** Opens a node of the map (a hypothesis's test). */
  onOpen?: ((nodeId: string) => void) | undefined;
}

/** What becomes of a prompt: the arms that answer it, what is read off the answers, and the hypotheses that feeds. */
export function PromptStrip({ lineage, runs, chosen, onOpen }: PromptStripProps) {
  if (!lineage.runs) return <p className="prompt-strip__none">This eval is not selected, so the study does not ask this prompt.</p>;
  if (!runs) return <p className="prompt-strip__none">The study stops before this prompt: it runs the first {count(chosen)} in the eval's own order. Raise the prompt count to include it.</p>;
  const deciding = lineage.outcomes.filter((outcome) => outcome.decides);
  const others = lineage.outcomes.filter((outcome) => !outcome.decides);
  return <ol className="prompt-strip" aria-label="What happens to this prompt">
    <li className="prompt-strip__step prompt-strip__step--arms">
      <span className="prompt-strip__what">Asked</span>
      <span>
        {lineage.arms.count === 0
          ? "No arm runs yet, so nothing answers it."
          : `${lineage.arms.count === 1 ? "The one arm answers" : `Each of the ${lineage.arms.count} arms answers`} it ${answersText(lineage.k, lineage.toldCells)}: ${listed(lineage.arms.parts)}.`}
      </span>
    </li>
    <li className="prompt-strip__step prompt-strip__step--outcome">
      <span className="prompt-strip__what">Read off</span>
      <span>
        {lineage.outcomes.length === 0 ? "No outcome is listed for this eval." : null}
        {deciding.length > 0 ? `${listed(deciding.map((outcome) => outcome.label))}, which ${deciding.length === 1 ? "decides" : "decide"} the study.` : null}
        {deciding.length > 0 && others.length > 0 ? " " : null}
        {others.length > 0 ? `${listed(others.map((outcome) => outcome.label))}${lineage.legacy ? "." : ", reported without a verdict."}` : null}
      </span>
    </li>
    <li className="prompt-strip__step prompt-strip__step--hypothesis">
      <span className="prompt-strip__what">Feeds</span>
      <span>
        {lineage.hypotheses.length > 0 ? null
          : lineage.legacy ? `${lineage.fixedPlan.length > 0 ? lineage.fixedPlan.join(" ") : "The fixed Study 1 plan lists no outcome for this eval."} The map does not draw that plan.`
            : "No hypothesis: no outcome of this eval decides the study."}
        {lineage.hypotheses.map((hypothesis) => <span key={hypothesis.id} className="prompt-strip__hypothesis">
          {onOpen
            ? <button type="button" className="text-button" onClick={() => onOpen(hypothesis.nodeId)}>{hypothesis.id}</button>
            : <strong>{hypothesis.id}</strong>}
          {" "}{hypothesis.statement}
        </span>)}
      </span>
    </li>
  </ol>;
}

interface PromptPaneProps {
  item: CardItem;
  runs: boolean;
  chosen: number;
  lineage: PromptLineage;
  onOpen?: ((nodeId: string) => void) | undefined;
}

/** One prompt in full, turn by turn as the model receives it, and what becomes of it. */
export function PromptPane({ item, runs, chosen, lineage, onOpen }: PromptPaneProps) {
  const cut = cutLength(item);
  return <article className={`prompt-pane${runs ? "" : " is-beyond"}`} aria-label={`Prompt ${item.index + 1}`} data-index={item.index}>
    <header className="prompt-pane__head">
      <strong>Prompt {item.index + 1}</strong>
      <code>{item.prompt_id}</code>
      <span className={`prompt-pane__standing${runs ? "" : " prompt-pane__standing--out"}`}>{runs ? "In this study" : "Not in this study"}</span>
    </header>
    {turnsOf(item).map((turn, index) => <section key={index} className={`prompt-turn prompt-turn--${turn.role || "other"}`}>
      <h5>{roleLabel(turn.role)}</h5>
      <p className="prompt-turn__text">{turn.content}</p>
    </section>)}
    {cut !== null ? <p className="prompt-pane__cut" role="note">This preview is cut at {count(cut)} characters per message. The run sends the full text.</p> : null}
    <PromptStrip lineage={lineage} runs={runs} chosen={chosen} onOpen={onOpen} />
  </article>;
}

interface PromptDeckProps {
  graph: StudyGraph;
  /** The eval's node. */
  node: MapNode;
  onOpen?: ((nodeId: string) => void) | undefined;
}

/**
 * The prompts of an eval: a list, the picked prompt in full, and the told-cell switch where the eval has told
 * cells. Reading them, their absence (an eval whose prompts cannot be listed before a run) and a request that
 * failed are each said in a sentence.
 */
export function PromptDeck({ graph, node, onOpen }: PromptDeckProps) {
  const ref = node.ref.type === "card" ? node.ref : null;
  const cardId = ref?.cardId ?? "";
  const hasTold = (ref?.toldCells ?? 1) > 1;
  const [told, setTold] = useState<Told>(DEFAULT_TOLD);
  const [picked, setPicked] = useState<number | null>(null);
  const group = useId();
  const query = useCardItems(cardId, hasTold ? told : null, ref !== null && ref.known);
  const lineage = useMemo(() => promptLineage(graph, cardId), [graph, cardId]);

  const pages = query.data?.pages;
  const head = pages?.[0];
  const items = useMemo(() => (pages ?? []).flatMap((page) => page.items), [pages]);
  const chosen = ref?.selected ? ref.prompts : 0;
  const selected = picked !== null && items.some((item) => item.index === picked) ? picked : items[0]?.index ?? null;
  const item = items.find((candidate) => candidate.index === selected);
  const { fetchNextPage, hasNextPage, isFetchingNextPage, isFetchNextPageError } = query;
  const readMore = useCallback(() => { void fetchNextPage(); }, [fetchNextPage]);

  if (ref === null) return null;
  const heading = <h4 className="prompt-deck__title">Prompts</h4>;
  if (!ref.known) {
    return <section className="prompt-deck" data-prompts="unknown" aria-label={`Prompts of ${node.label}`}>
      {heading}
      <p className="prompt-deck__state">This eval is not in the registry of this page, so its prompts cannot be listed.</p>
    </section>;
  }

  const answers = answersText(ref.k, ref.toldCells);
  const summary = head?.listable
    ? ref.selected
      ? chosen >= head.total
        ? `The study runs all ${count(head.total)} prompts, each answered ${answers} by every arm.`
        : `The study runs the first ${count(chosen)} of ${count(head.total)} prompts, in the eval's own order, each answered ${answers} by every arm.`
      : `This eval has ${count(head.total)} prompts. It is not selected, so the study runs none of them.`
    : null;

  return <section
    className="prompt-deck"
    aria-label={`Prompts of ${node.label}`}
    data-prompts={query.isPending ? "loading" : query.isError ? "failed" : head?.listable ? "listed" : "unlisted"}
    data-source={head?.source}
  >
    {heading}
    {hasTold ? <div className="prompt-told" role="radiogroup" aria-label="Told cell">
      {TOLD_CELLS.map((cell) => <label key={cell.value} className={`prompt-told__cell${told === cell.value ? " is-on" : ""}`} title={cell.says}>
        <input type="radio" name={group} value={cell.value} checked={told === cell.value} onChange={() => setTold(cell.value)} />
        <span>{cell.label}</span>
      </label>)}
      <p className="prompt-told__says">{TOLD_CELLS.find((cell) => cell.value === told)?.says} Every arm answers each prompt in all three cells.</p>
    </div> : null}
    {query.isPending ? <p className="prompt-deck__state" role="status">Reading the prompts from the run service.</p> : null}
    {query.isError ? <div className="prompt-deck__state prompt-deck__state--failed" role="alert">
      <p>{promptFailure(query.error)}</p>
      {/* Where only a later page failed, what was read stays listed and the list's own button asks again. */}
      {isFetchNextPageError
        ? <p>The prompts read so far are still listed. The button under the list asks for the next ones again.</p>
        : <button type="button" className="text-button" disabled={query.isFetching} onClick={() => { void query.refetch(); }}>{query.isFetching ? "Trying again…" : "Try again"}</button>}
    </div> : null}
    {head !== undefined && !head.listable ? <p className={`prompt-deck__state prompt-deck__state--${head.source}`}>{sourceSentence(head.source, head.reason)}</p> : null}
    {head?.listable ? <>
      <p className="prompt-deck__summary">{summary}</p>
      {items.length === 0 ? <p className="prompt-deck__state">This eval lists no prompt.</p> : <div className="prompt-deck__split">
        <PromptList
          key={told}
          label={`Prompts of ${node.label}`}
          items={items}
          total={head.total}
          chosen={chosen}
          selected={selected}
          onSelect={setPicked}
          more={hasNextPage}
          loadingMore={isFetchingNextPage}
          moreFailed={isFetchNextPageError}
          onMore={readMore}
        />
        {item ? <PromptPane item={item} runs={inStudy(item.index, chosen)} chosen={chosen} lineage={lineage} onOpen={onOpen} /> : null}
      </div>}
    </> : null}
  </section>;
}
