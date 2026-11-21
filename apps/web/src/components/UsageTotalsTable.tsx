import type { ReactNode } from "react";
import type { UsageBucketView } from "@qr-evals/contracts";

const number = new Intl.NumberFormat("en-US");
const dollars = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
export function formatTokens(value: number): string { return number.format(value); }

/** Millionths of a dollar as dollars and cents; a non-zero amount under a cent says so. */
export function formatUsd(microusd: number): string {
  if (microusd > 0 && microusd < 5000) return "under $0.01";
  return dollars.format(microusd / 1_000_000);
}

const UNITS = ["B", "KB", "MB", "GB", "TB"] as const;
export function formatBytes(bytes: number): string {
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) { value /= 1024; unit += 1; }
  return `${unit === 0 || value >= 100 ? Math.round(value) : value.toFixed(1)} ${UNITS[unit]}`;
}

interface UsageTotalsTableProps { caption: string; keyLabel: string; buckets: readonly UsageBucketView[]; empty: string; labelFor?: (key: string) => ReactNode }

/** One usage grouping: input and output tokens on the shared engine, QRNG and PRNG apart, and their price. */
export function UsageTotalsTable({ caption, keyLabel, buckets, empty, labelFor }: UsageTotalsTableProps) {
  if (buckets.length === 0) return <p className="usage-empty">{empty}</p>;
  const ownKey = buckets.reduce((sum, bucket) => sum + bucket.totals.own_key_tokens, 0);
  return <>
    <div className="table-frame usage-table"><table>
      <caption className="sr-only">{caption}</caption>
      <thead><tr><th scope="col">{keyLabel}</th><th scope="col" className="num">QRNG in</th><th scope="col" className="num">QRNG out</th><th scope="col" className="num">PRNG in</th><th scope="col" className="num">PRNG out</th><th scope="col" className="num">Cost</th></tr></thead>
      <tbody>{buckets.map(({ key, totals }) => <tr key={key}>
        <th scope="row">{labelFor ? labelFor(key) : key}</th>
        <td className="num">{formatTokens(totals.qrng_input)}</td>
        <td className="num">{formatTokens(totals.qrng_output)}</td>
        <td className="num">{formatTokens(totals.prng_input)}</td>
        <td className="num">{formatTokens(totals.prng_output)}</td>
        <td className="num">{formatUsd(totals.cost_microusd)}</td>
      </tr>)}</tbody>
    </table></div>
    {ownKey > 0 ? <p className="usage-note">Plus {formatTokens(ownKey)} tokens on your own provider key. Your provider bills those; they are not priced here.</p> : null}
  </>;
}
