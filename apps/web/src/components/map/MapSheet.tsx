import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode, type Ref } from "react";
import { STATIONS } from "../../lib/map/graph";
import type { PopoverSide } from "../../lib/map/popover";
import type { Box } from "../../lib/map/viewport";
import { seedFromId } from "../../lib/prng";
import { roughLine, roughRect } from "../../lib/rough";
import type { Subject } from "./Inspector";
import { StatePip } from "./MapChip";
import { stateWords } from "./model";

interface MapSheetProps {
  subject: Subject;
  /** Where the popover hangs in the world: under what it is about, or beside it. */
  box: Box;
  side: PopoverSide;
  /** The popover's element, for the canvas that keeps it inside the viewport. */
  frame?: Ref<HTMLElement>;
  /** Whether the focus goes to the close button when it opens. Not when a field is to take it. */
  takeFocus: boolean;
  onClose: () => void;
  /** The body and the foot: what the popover says and edits. */
  children: ReactNode;
}

const TAB_STOPS = "a[href], button, input, select, textarea, summary, [tabindex]";

/**
 * What the Tab key stops at inside a popover, in order. It is read from the markup, not from the layout: nothing
 * disabled or hidden, nothing inside a folded `<details>` but its summary, and of a group of radio buttons only the
 * one the Tab key lands on.
 */
export function tabStops(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(TAB_STOPS)].filter((element) => {
    if (element.tabIndex < 0 || element.hasAttribute("disabled") || element.closest("[hidden]")) return false;
    const folded = element.closest("details:not([open])");
    if (folded !== null && !(element.tagName === "SUMMARY" && element.parentElement === folded)) return false;
    if (element instanceof HTMLInputElement && element.type === "radio" && element.name !== "") {
      const group = [...root.querySelectorAll<HTMLInputElement>("input[type=radio]")].filter((radio) => radio.name === element.name && !radio.disabled);
      return element === (group.find((radio) => radio.checked) ?? group[0]);
    }
    return true;
  });
}

// The outline is drawn in a 100 × 100 box and stretched to the popover, with a stroke that keeps its width.
const OUTLINE = { size: 100, inset: 1.2 };

/**
 * The popover of an opened node or note: a pencilled sheet that hangs on what it is about, in the world, so it
 * moves with the map. The canvas keeps it inside the viewport through the CSS variables `--pop-dx`, `--pop-dy`,
 * `--pop-s`, `--pop-max-w` and `--pop-max-h`.
 */
export function MapSheet({ subject, box, side, frame, takeFocus, onClose, children }: MapSheetProps) {
  const titleId = useId();
  const close = useRef<HTMLButtonElement>(null);
  const station = STATIONS.find((item) => item.id === subject.station);
  const seed = seedFromId(`popover:${subject.id}`);

  // The popover takes the focus when it opens; closing it hands the focus back to what opened it (the map does that).
  useEffect(() => { if (takeFocus) close.current?.focus({ preventScroll: true }); }, [subject.id, takeFocus]);

  // The popover is a dialog: while it is open the Tab key goes round inside it, so the focus cannot wander off to
  // the page and leave it open behind. Escape, or its close button, puts it away and returns the focus.
  function onKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key !== "Tab" || event.altKey || event.ctrlKey || event.metaKey) return;
    const stops = tabStops(event.currentTarget);
    const first = stops[0];
    const last = stops[stops.length - 1];
    if (!first || !last) return;
    const active = document.activeElement;
    if (event.shiftKey ? active === first : active === last) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus({ preventScroll: true });
    }
  }

  return <section
    className={`map-sheet map-popover map-sheet--${subject.kind} map-popover--${side}`}
    role="dialog"
    aria-labelledby={titleId}
    data-sheet={subject.id}
    data-side={side}
    ref={frame}
    onKeyDown={onKeyDown}
    style={{
      transform: `translate(calc(${box.x}px + var(--pop-dx, 0px)), calc(${box.y}px + var(--pop-dy, 0px))) scale(var(--pop-s, 1))`,
      width: `min(${box.w}px, var(--pop-max-w, ${box.w}px))`,
      maxHeight: `min(${box.h}px, var(--pop-max-h, ${box.h}px))`,
    }}
  >
    <svg className="map-popover__outline" viewBox={`0 0 ${OUTLINE.size} ${OUTLINE.size}`} preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <path d={roughRect(OUTLINE.inset, OUTLINE.inset, OUTLINE.size - 2 * OUTLINE.inset, OUTLINE.size - 2 * OUTLINE.inset, { seed, roughness: 0.7, radius: 2.5 })} />
    </svg>
    {/* A pencil stroke from the popover to what it is about. It is hidden when the popover has been moved off it. */}
    <svg className="map-popover__tail" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d={side === "below" ? roughLine(12, 23, 12, 3, { seed, roughness: 0.9 }) : roughLine(23, 12, 3, 12, { seed, roughness: 0.9 })} />
    </svg>
    <header className="map-sheet__head">
      <p className="map-sheet__station">{station?.label ?? subject.station}{subject.note !== null ? " · note" : ""}</p>
      <h3 id={titleId}>{subject.label}</h3>
      <p className="map-sheet__sub">
        {subject.state !== null ? <><StatePip state={subject.state} /> <span>{stateWords(subject.state)}.</span> </> : null}
        {subject.sub}
      </p>
      <button type="button" className="map-sheet__close" ref={close} aria-label={`Close the details of ${subject.label}`} onClick={onClose}><span aria-hidden="true">×</span></button>
    </header>
    {children}
  </section>;
}
