import { useState } from "react";
import { setAuthors, setContact, setTitle } from "../../../lib/config-edits";
import { Field, type FieldProps } from "./Field";

// The fields that say whose study this is.

export function TitleField({ config, updateConfig }: FieldProps) {
  return <Field path="title" label="Study title">
    <input className="study-title" value={config.title} onChange={(event) => updateConfig(setTitle(config, event.target.value))} />
  </Field>;
}

/** The authors, one per line. Blank lines are not authors. */
export function AuthorsField({ config, updateConfig }: FieldProps) {
  // What is typed is kept while the box has the focus, so a new line can be started before it has a name on it.
  const [draft, setDraft] = useState<string | null>(null);
  return <Field path="authors[]" label="Authors" note="One per line.">
    <textarea
      rows={Math.max(2, config.authors.length + 1)}
      value={draft ?? config.authors.join("\n")}
      onChange={(event) => {
        setDraft(event.target.value);
        updateConfig(setAuthors(config, event.target.value.split("\n").map((line) => line.trim()).filter((line) => line !== "")));
      }}
      onBlur={() => setDraft(null)}
    />
  </Field>;
}

export function ContactField({ config, updateConfig }: FieldProps) {
  return <Field path="contact" label="Contact" note="Printed in the pre-registration: an address a reader can write to.">
    <input value={config.contact} onChange={(event) => updateConfig(setContact(config, event.target.value))} />
  </Field>;
}
