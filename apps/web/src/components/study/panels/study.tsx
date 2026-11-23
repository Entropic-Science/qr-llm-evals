import { AuthorsField, ContactField, TitleField } from "../../../routes/configure/fields/study";
import { definePanel } from "./Panel";

export const titlePanel = definePanel(
  { id: "title", section: "study", label: "Title", paths: ["title"] },
  (props) => <TitleField {...props} />,
);

export const peoplePanel = definePanel(
  { id: "people", section: "study", label: "Authors and contact", paths: ["authors[]", "contact"] },
  (props) => <><AuthorsField {...props} /><ContactField {...props} /></>,
);
