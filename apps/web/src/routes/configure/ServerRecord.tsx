import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { HashBadge } from "../../components/HashBadge";
import { api, ApiError } from "../../lib/api";
import { parseStudyConfig, type WizardStudyConfig } from "../../lib/config-store";
import { useStudy } from "./StudyContext";

// What the run service says about the study as it stands: its three hashes, or its refusal.
//
// The browser computes no hash of its own. The service is asked through the pre-registration render call,
// only while the Review step is on screen, only for a study this page can serialise, and only after the
// study has been left alone for a moment. An answer is about one study: the next edit retires it.

/** How long the study has to stay unchanged before the service is asked. */
export const SERVER_RECORD_DEBOUNCE_MS = 400;

export type ServerRecord =
  /** Not asked: the Review step is not on screen. */
  | { kind: "idle" }
  /** Not asked: the draft is not a study this page can serialise. */
  | { kind: "not_serialisable" }
  | { kind: "pending" }
  | { kind: "hashes"; configHash: string; planHash: string; preregHash: string }
  /** The service read the study and refused it. The message is also the `review.server_rejected` problem. */
  | { kind: "refused"; message: string }
  /** No answer about the study: the service was not reached, or answered with something other than a verdict on it. */
  | { kind: "no_answer"; reached: boolean; message: string };

interface ServerRecordValue {
  record: ServerRecord;
  /** Asks again for the study as it stands. */
  ask: () => void;
}

const ServerRecordContext = createContext<ServerRecordValue | null>(null);

export function useServerRecord(): ServerRecordValue {
  const value = useContext(ServerRecordContext);
  if (value === null) throw new Error("useServerRecord() is called outside <ServerRecordProvider>.");
  return value;
}

/** The statuses with which the service says "this study, as sent, is not one I accept". */
const REFUSAL_STATUSES: readonly number[] = [400, 422];

interface Answer {
  /** The study the record is about, by identity: every edit makes a new object. */
  config: WizardStudyConfig | null;
  record: ServerRecord;
}

export function ServerRecordProvider({ children }: { children: ReactNode }) {
  const { config, step, setServerError } = useStudy();
  const [answer, setAnswer] = useState<Answer>({ config: null, record: { kind: "idle" } });
  const onReview = step.id === "review";
  const serialisable = useMemo(() => parseStudyConfig(config) !== null, [config]);
  const answered = answer.config === config;

  useEffect(() => {
    if (answered) return;
    // Whatever the service said before, it said about another study.
    setServerError(null);
    if (!onReview || !serialisable) return;
    let live = true;
    const timer = window.setTimeout(() => {
      api.prereg({ config }).then(
        (response) => {
          if (live) setAnswer({ config, record: { kind: "hashes", configHash: response.config_hash, planHash: response.plan_hash, preregHash: response.prereg_hash } });
        },
        (cause: unknown) => {
          if (!live) return;
          if (cause instanceof ApiError && REFUSAL_STATUSES.includes(cause.status)) {
            setAnswer({ config, record: { kind: "refused", message: cause.message } });
            setServerError(cause.message);
            return;
          }
          const reached = cause instanceof ApiError;
          setAnswer({ config, record: { kind: "no_answer", reached, message: cause instanceof Error ? cause.message : "" } });
        },
      );
    }, SERVER_RECORD_DEBOUNCE_MS);
    return () => { live = false; window.clearTimeout(timer); };
  }, [answered, config, onReview, serialisable, setServerError]);

  const ask = useCallback(() => setAnswer({ config: null, record: { kind: "idle" } }), []);
  const record = useMemo<ServerRecord>(() => {
    if (answered) return answer.record;
    if (!serialisable) return { kind: "not_serialisable" };
    return onReview ? { kind: "pending" } : { kind: "idle" };
  }, [answered, answer.record, serialisable, onReview]);
  const value = useMemo<ServerRecordValue>(() => ({ record, ask }), [record, ask]);
  return <ServerRecordContext.Provider value={value}>{children}</ServerRecordContext.Provider>;
}

function note(record: ServerRecord): string {
  switch (record.kind) {
    case "hashes": return "Computed by the run service for the study as it stands. Showing them registers nothing: the study is frozen when it is launched. The pre-registration hash is of the draft rendered without a readiness report, so the document frozen at launch has a hash of its own.";
    case "pending": return "Asking the run service for the hashes of this study.";
    case "not_serialisable": return "This draft is not a valid study yet, so there is nothing to hash.";
    case "refused": return "The run service refused this study, so it has no hashes. Its message is listed with the problems above and blocks the launch.";
    case "no_answer": return record.reached
      ? `The run service gave no hashes${record.message ? `: ${record.message}${/[.!?]$/.test(record.message) ? "" : "."}` : "."}`
      : "Hashes need the run service. It is not reachable.";
    case "idle": return "The hashes are asked for while this step is open.";
  }
}

/**
 * The server's hashes of the study: canonical config, statistical plan and pre-registration draft.
 *
 * The region is announced politely when an answer arrives. A refusal is not repeated here as an alert: it is
 * a blocking problem in the list above.
 */
export function ServerHashes() {
  const { record, ask } = useServerRecord();
  const hashes = record.kind === "hashes" ? record : null;
  const pending = record.kind === "pending";
  return <section className="server-record" aria-labelledby="server-record-title">
    <h3 id="server-record-title">Server hashes</h3>
    <div aria-live="polite" data-record={record.kind}>
      <dl className="hash-ledger">
        <HashBadge label="Canonical config · SHA-256" hash={hashes?.configHash} pending={pending} />
        <HashBadge label="Statistical plan · SHA-256" hash={hashes?.planHash} pending={pending} />
        <HashBadge label="Pre-registration draft · SHA-256" hash={hashes?.preregHash} pending={pending} />
      </dl>
      <p className="hash-ledger__note">{note(record)}</p>
      {record.kind === "no_answer" ? <button type="button" className="text-button" onClick={ask}>Ask again</button> : null}
    </div>
  </section>;
}
