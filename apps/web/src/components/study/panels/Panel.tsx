import { useContext, type ReactNode } from "react";
import type { PanelDef, PanelProps } from "../../../lib/panels";
import { FieldScope } from "../../../routes/configure/fields/Field";

/** The element id of a panel within a field scope, for the settings page's section links. */
export function panelId(id: string, scope = ""): string {
  return `${scope}panel-${id}`;
}

function PanelFrame({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  const scope = useContext(FieldScope);
  return <fieldset className="settings-panel" id={panelId(id, scope)}>
    <legend>{label}</legend>
    {children}
  </fieldset>;
}

/**
 * A settings panel: what it is called, which study key paths it edits, and its fields.
 *
 * The registry entry and the component are made together, so a panel cannot be rendered under one name and
 * registered under another. `Component` draws the fields inside the panel's own fieldset.
 */
export function definePanel(meta: Omit<PanelDef, "Component">, Body: (props: PanelProps) => ReactNode): PanelDef {
  const Component = (props: PanelProps) => <PanelFrame id={meta.id} label={meta.label}><Body {...props} /></PanelFrame>;
  Component.displayName = `Panel(${meta.id})`;
  return { ...meta, Component };
}
