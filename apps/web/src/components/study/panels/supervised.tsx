import type { ReactNode } from "react";
import type { PanelProps } from "../../../lib/panels";
import {
  DisplaySettingsField, GroupSessionField, IntentionTimerField, RefocusField, SessionDisplayField,
} from "../../../routes/configure/fields/instruments";
import { definePanel } from "./Panel";

const DISPLAY_KEYS = ["session_min", "break_min", "streams", "text_size", "show_thinking"] as const;

/** The session fields, or the note that stands in for them on a study without supervised sessions. */
function supervised(props: PanelProps, fields: ReactNode): ReactNode {
  return props.config.supervised === null || props.config.supervised === undefined
    ? <p className="field-note">This study has no supervised sessions. They are switched on under Comparisons.</p>
    : fields;
}

export const sessionsPanel = definePanel(
  {
    id: "sessions", section: "supervised", label: "Supervised sessions",
    paths: ["supervised.display", "supervised.intention_timer_min", "supervised.refocus_s", "supervised.group"],
  },
  (props) => supervised(props, <>
    <p className="field-note">Observation is host-paced. Timers never start or resume a session; a person always does.</p>
    <SessionDisplayField {...props} />
    <IntentionTimerField {...props} />
    <RefocusField {...props} />
    <GroupSessionField {...props} />
  </>),
);

export const displaySettingsPanel = definePanel(
  {
    id: "display-settings", section: "supervised", label: "Session displays", advanced: true,
    paths: (["single", "tiled"] as const).flatMap((mode) => DISPLAY_KEYS.map((key) => `supervised.settings_by_display.${mode}.${key}`)),
  },
  (props) => supervised(props, <>
    <DisplaySettingsField {...props} mode="single" />
    <DisplaySettingsField {...props} mode="tiled" />
  </>),
);
