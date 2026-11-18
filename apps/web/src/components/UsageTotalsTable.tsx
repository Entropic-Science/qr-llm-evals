import type { UsageBucketView } from "@qr-evals/contracts";

const number = new Intl.NumberFormat("en-US");
export function formatTokens(value: number): string { return number.format(value); }

interface UsageTotalsTableProps { caption: string; keyLabel: string; buckets: readonly UsageBucketView[]; empty: string; labelFor?: (key: string) => string }

/** One usage grouping: calls and prompt / completion / charged tokens per key. */
export function UsageTotalsTable({ caption, keyLabel, buckets, empty, labelFor }: UsageTotalsTableProps) {
  if (buckets.length === 0) return <p className="usage-empty">{empty}</p>;
  return <div className="table-frame usage-table"><table>
    <caption className="sr-only">{caption}</caption>
    <thead><tr><th scope="col">{keyLabel}</th><th scope="col" className="num">Calls</th><th scope="col" className="num">Prompt</th><th scope="col" className="num">Completion</th><th scope="col" className="num">Counted against quota</th></tr></thead>
    <tbody>{buckets.map(({ key, totals }) => <tr key={key}>
      <th scope="row">{labelFor ? labelFor(key) : key}</th>
      <td className="num">{formatTokens(totals.calls)}</td>
      <td className="num">{formatTokens(totals.prompt_tokens)}</td>
      <td className="num">{formatTokens(totals.completion_tokens)}</td>
      <td className="num">{formatTokens(totals.charged_tokens)}</td>
    </tr>)}</tbody>
  </table></div>;
}
