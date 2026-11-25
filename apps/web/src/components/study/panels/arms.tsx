import {
  ArmTable, CellGridField, ControlSeedsField, CustomArmsNotice, DeterministicField, EpsilonArmsField, QuantumArmField,
  SeedValuesField, SupervisedToggleField, rosterOf,
} from "../../../routes/configure/fields/compare";
import {
  ConditioningHashField, ConditioningInBytesField, ConditioningKeepBytesField, ConditioningModeField, ConditioningSummary,
} from "../../../routes/configure/fields/conditioning";
import { definePanel } from "./Panel";

/**
 * What is compared, as roles. It rewrites the arm list, and with it the models, the seed list and the
 * supervised block, so it claims those lists whole; their single fields belong to the advanced panels.
 */
export const comparisonPanel = definePanel(
  { id: "comparison", section: "arms", label: "Comparisons", paths: ["arms", "models", "seeds", "supervised"] },
  (props) => {
    if (rosterOf(props) === null) return <CustomArmsNotice {...props} />;
    return <>
      <CellGridField {...props} />
      <QuantumArmField {...props} />
      <ControlSeedsField {...props} />
      <EpsilonArmsField {...props} />
      <SupervisedToggleField {...props} />
      <details className="settings-details">
        <summary>Advanced</summary>
        <SeedValuesField {...props} />
        <DeterministicField {...props} />
        <ArmTable config={props.config} />
      </details>
    </>;
  },
);

/**
 * How the entropy server's bytes reach the arms that draw from it. The page edits the parameters of hashed
 * entropy and states the mode: raw is written in a study file, never chosen here.
 * It belongs with the comparison because it decides what the quantum arm is compared on.
 */
export const conditioningPanel = definePanel(
  {
    id: "conditioning", section: "arms", label: "Entropy conditioning",
    paths: ["conditioning.mode", "conditioning.hash", "conditioning.in_bytes", "conditioning.keep_bytes"],
  },
  (props) => <>
    <ConditioningModeField {...props} />
    <ConditioningHashField {...props} />
    <ConditioningInBytesField {...props} />
    <ConditioningKeepBytesField {...props} />
    <ConditioningSummary {...props} />
  </>,
);
