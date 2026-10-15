interface HashBadgeProps {
  label: string;
  hash?: string | undefined;
  pending?: boolean;
}

export function HashBadge({ label, hash, pending = false }: HashBadgeProps) {
  return <div className="hash-badge">
    <dt>{label}</dt>
    <dd>
      <span className="hash-badge__mark" aria-hidden="true" />
      <code title={hash}>{pending ? "calculating…" : (hash ?? "not rendered")}</code>
    </dd>
  </div>;
}
