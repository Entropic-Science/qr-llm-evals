import type { FileMark, FileState } from "../../lib/study-file";
import type { Problem } from "../../lib/problems";

// How a study stands, in the words the study bar, the settings dialog and the load report share.
//
// A problem that blocks a launch is a thing "to fix"; a warning is a thing "to check". The same two words are
// on the step rail, so a count reads the same wherever it is shown.

export interface Standing {
  /** Problems that block a launch. */
  errors: number;
  /** Problems that do not. */
  warnings: number;
}

export function standingOf(problems: readonly Problem[]): Standing {
  const errors = problems.filter((problem) => problem.severity === "error").length;
  return { errors, warnings: problems.length - errors };
}

/** "1 thing to fix", "3 things to fix". */
export function thingsToFix(count: number): string {
  return `${count} ${count === 1 ? "thing" : "things"} to fix`;
}

/** The validity pill: "Ready", "Ready · 1 to check", "2 things to fix". */
export function standingText({ errors, warnings }: Standing): string {
  if (errors > 0) return thingsToFix(errors);
  return warnings > 0 ? `Ready · ${warnings} to check` : "Ready";
}

/** How a list of problems stands, for a section of the settings: "1 to fix, 2 to check", "1 to check" or "clear". */
export function sectionStanding({ errors, warnings }: Standing): string {
  if (errors > 0) return warnings > 0 ? `${errors} to fix, ${warnings} to check` : `${errors} to fix`;
  return warnings > 0 ? `${warnings} to check` : "clear";
}

/**
 * What the page knows of the study's file, in two words. "Saved to" is said only of a file the page wrote
 * itself and saw closed, or one the researcher picked to load. A download is a file the page handed to the
 * browser: whether it was written, and under which name, the page cannot tell, so it says "Downloaded as".
 */
export function fileOriginText(mark: FileMark): string {
  return mark.origin === "downloaded" ? "Downloaded as" : "Saved to";
}

/** What the file state ends with while the browser is not storing the draft, whatever the reason. */
export const NOT_KEPT = "edits are not kept in this browser";

/**
 * Where the study stands against its file: "Not saved to a file yet", "Saved to <name>" (or "Downloaded as
 * <name>") or "Changed since <name>". A saved study that still has blocking problems says so, because a file
 * can be saved at any time and being saved does not make it ready; a file that was checked and will not load
 * back says that instead, because it is the more urgent of the two.
 *
 * `stored` is whether the browser holds the draft as it is on screen. While it does not, the state says so
 * after everything else: the file is then the only place the latest edits could be.
 */
export function fileStateText(state: FileState, mark: FileMark | null, standing: Standing, stored = true): string {
  const text = fileText(state, mark, standing);
  return stored ? text : `${text} · ${NOT_KEPT}`;
}

function fileText(state: FileState, mark: FileMark | null, standing: Standing): string {
  if (state === "none" || mark === null) return "Not saved to a file yet";
  if (state === "changed") return `Changed since ${mark.name}`;
  const saved = `${fileOriginText(mark)} ${mark.name}`;
  if (mark.loadsBack === false) return `${saved} · will not load back as it is`;
  return standing.errors > 0 ? `${saved} · ${thingsToFix(standing.errors)}` : saved;
}
