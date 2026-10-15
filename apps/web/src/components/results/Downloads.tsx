import { downloadUrl } from "../../lib/results";
export function Downloads({ studyId }: { studyId: string }) {
  return (
    <section className="results-downloads">
      <h2>Data & licence</h2>
      <p>
        Published data are licensed under{" "}
        <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>:
        share and adapt with attribution, a licence link, and an indication of
        changes. Published prose is CC BY-SA 4.0. Preserve provenance when
        reanalysing or redistributing results; consult the archived licence for
        the full terms.
      </p>
      <ul>
        {[
          ["bundle.tar.zst", "Complete reproducibility bundle"],
          ["prereg.md", "Published preregistration (Markdown)"],
          ["LICENSE-data", "Data licence (LICENSE-data)"],
        ].map(([file, label]) => (
          <li key={file}>
            <a href={downloadUrl(studyId, file!)}>{label}</a>
          </li>
        ))}
      </ul>
    </section>
  );
}
