import {
  ArmTableField, CreatedAtField, EstimateSessionField, ModelsTableField, PilotEnabledField, PilotPromptsField, ReadingSpeedField,
  RecordFields, SeedListField, ThroughputField, TiledStreamsField,
} from "../../../routes/configure/fields/advanced";
import { definePanel } from "./Panel";

const ARM_KEYS = ["id", "model", "sampling", "source", "study_seed", "epsilon", "deterministic", "observer_mode", "availability", "unavailable_reason"];

/** Every arm as written. The editor for arm sets the Comparisons panel cannot show as roles. */
export const armTablePanel = definePanel(
  { id: "arm-table", section: "advanced", label: "Arm table", advanced: true, paths: ARM_KEYS.map((key) => `arms[].${key}`) },
  (props) => <ArmTableField {...props} />,
);

export const modelsPanel = definePanel(
  {
    id: "models", section: "advanced", label: "Models", advanced: true,
    paths: ["id", "family", "provider", "served_name", "checkpoint"].map((key) => `models[].${key}`),
  },
  (props) => <ModelsTableField {...props} />,
);

export const seedListPanel = definePanel(
  { id: "seed-list", section: "advanced", label: "Seeds", advanced: true, paths: ["seeds[]"] },
  (props) => <SeedListField {...props} />,
);

export const estimatesPanel = definePanel(
  {
    id: "estimates", section: "advanced", label: "Estimate assumptions", advanced: true,
    paths: ["estimates.tps_by_model.*", "estimates.reading_tps", "estimates.tiled_streams", "estimates.session_min"],
  },
  (props) => <>
    <ThroughputField {...props} />
    <ReadingSpeedField {...props} />
    <TiledStreamsField {...props} />
    <EstimateSessionField {...props} />
  </>,
);

export const pilotPanel = definePanel(
  { id: "pilot", section: "advanced", label: "Pilot", advanced: true, paths: ["pilot.enabled", "pilot.prompts"] },
  (props) => <><PilotEnabledField {...props} /><PilotPromptsField {...props} /></>,
);

/**
 * Values the study records: they are shown so that nothing in the file is hidden. The configuration date can
 * be set to now; the rest nobody sets here.
 */
export const recordPanel = definePanel(
  {
    id: "record", section: "advanced", label: "Recorded with the study", advanced: true,
    paths: [
      "schema_version", "template", "created_at", "data_licence", "design.version", "judge.rubric_hash",
      "supervised.target_pool_hash", "supervised.questionnaires_hash",
    ],
  },
  (props) => <><CreatedAtField {...props} /><RecordFields {...props} /></>,
);
