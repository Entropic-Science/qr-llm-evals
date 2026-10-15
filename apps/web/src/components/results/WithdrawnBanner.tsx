export function WithdrawnBanner({ withdrawn }: { withdrawn: boolean }) {
  return withdrawn ? (
    <aside className="results-notice" role="alert">
      <strong>Withdrawn study or run</strong>
      <p>
        This record has been withdrawn. Preserved artifacts remain available for
        audit; do not treat this result as an active publication.
      </p>
    </aside>
  ) : null;
}
