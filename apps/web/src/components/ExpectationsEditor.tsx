interface ExpectationsEditorProps {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  showError?: boolean;
}

export function ExpectationsEditor({ value, onChange, disabled = false, showError = false }: ExpectationsEditorProps) {
  const invalid = showError && value.trim().length === 0;
  return <section className="expectations-editor" aria-labelledby="expectations-title">
    <div className="expectations-editor__heading">
      <div>
        <p className="eyebrow">Required record</p>
        <h2 id="expectations-title">Researcher expectations</h2>
      </div>
      <span className="expectations-editor__required">Required</span>
    </div>
    <p id="expectations-help">State what you expect before observing results. This statement is frozen into the document as context; it is <strong>not treated as evidence</strong>.</p>
    <label htmlFor="researcher-expectations" className="sr-only">Researcher expectations</label>
    <textarea
      id="researcher-expectations"
      aria-describedby={invalid ? "expectations-help expectations-error" : "expectations-help"}
      aria-invalid={invalid}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      placeholder="Before seeing the results, we expect…"
      rows={8}
      value={value}
    />
    <div className="expectations-editor__footer">
      <span>{value.trim().length.toLocaleString()} characters recorded</span>
      {invalid && <strong id="expectations-error" role="alert">Enter expectations before rendering.</strong>}
    </div>
  </section>;
}
