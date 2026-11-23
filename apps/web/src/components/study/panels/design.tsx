import type { ReactNode } from "react";
import type { PanelProps } from "../../../lib/panels";
import {
  AlphaField, BuiltInPlanField, ClaimField, DeclareDesignField, ExpectationsField, MarginsField, NoDesignNote, OutcomesField,
  SensitivityEpsilonField,
} from "../../../routes/configure/fields/design";
import { definePanel } from "./Panel";

/** A design field, or the note that stands in for it on a study without a design block. */
function designed(props: PanelProps, field: ReactNode): ReactNode {
  return props.config.design === null || props.config.design === undefined ? <NoDesignNote /> : field;
}

/** The claim, and the way into a design block and out of it again: it claims `design` whole for that. */
export const claimPanel = definePanel(
  { id: "claim", section: "design", label: "Claim", paths: ["design", "design.claim"] },
  (props) => <><DeclareDesignField {...props} /><ClaimField {...props} /><BuiltInPlanField {...props} /></>,
);

export const outcomesPanel = definePanel(
  { id: "outcomes", section: "design", label: "Outcomes", paths: ["design.confirmatory[].card_id", "design.confirmatory[].endpoint"] },
  (props) => designed(props, <OutcomesField {...props} />),
);

export const alphaPanel = definePanel(
  { id: "alpha", section: "design", label: "False-finding rate", paths: ["design.alpha"] },
  (props) => designed(props, <AlphaField {...props} />),
);

export const positiveControlPanel = definePanel(
  { id: "positive-control", section: "design", label: "Positive control", paths: ["design.sensitivity_epsilon"] },
  (props) => designed(props, <SensitivityEpsilonField {...props} />),
);

export const marginsPanel = definePanel(
  { id: "margins", section: "design", label: "Margins", paths: ["design.margins[].card_id", "design.margins[].endpoint", "design.margins[].margin"] },
  (props) => designed(props, <MarginsField {...props} />),
);

export const expectationsPanel = definePanel(
  { id: "expectations", section: "design", label: "Expectations", paths: ["expectations"] },
  (props) => <ExpectationsField {...props} />,
);
