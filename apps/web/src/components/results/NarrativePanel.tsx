import { Markdown } from "../Markdown";
export function NarrativePanel({
  published,
  narrative,
}: {
  published: boolean;
  narrative: string | null;
}) {
  return (
    <section className="results-narrative">
      <p className="eyebrow">Interpretation | LLM-written</p>
      <h2>Analysis narrative</h2>
      {published && narrative ? (
        <>
          <p>
            Numbers verified against the script-generated tables. Interpretation
            is model-written.
          </p>
          <Markdown source={narrative} />
        </>
      ) : (
        <p role="status">withheld: unverified numbers</p>
      )}
    </section>
  );
}
