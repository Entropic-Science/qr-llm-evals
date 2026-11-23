import {
  ArmTable, CellGridField, ControlSeedsField, CustomArmsNotice, DeterministicField, EpsilonArmsField, QuantumArmField,
  SeedValuesField, SupervisedToggleField, rosterOf,
} from "../../../routes/configure/fields/compare";
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
