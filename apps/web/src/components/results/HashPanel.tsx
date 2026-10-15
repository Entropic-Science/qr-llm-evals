import { useState } from "react";
export function HashPanel({
  prereg,
  config,
  plan,
}: {
  prereg?: string | undefined;
  config?: string | undefined;
  plan?: string | undefined;
}) {
  const [status, setStatus] = useState("");
  async function copy(label: string, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setStatus(`${label} hash copied`);
    } catch {
      setStatus("Copy unavailable. Select and copy the hash manually.");
    }
  }
  return (
    <section aria-label="Immutable hashes" className="results-hashes">
      <dl>
        {[
          ["Preregistration", prereg],
          ["Configuration", config],
          ["Analysis plan", plan],
        ].map(([label, value]) => (
          <div key={label}>
            <dt>{label} | SHA-256</dt>
            <dd>
              <code>{value ?? "Unavailable"}</code>
              <button
                className="button button--secondary"
                disabled={!value}
                onClick={() => void copy(label!, value!)}
                aria-label={`Copy ${label} hash`}
              >
                Copy
              </button>
            </dd>
          </div>
        ))}
      </dl>
      <p role="status">{status}</p>
    </section>
  );
}
