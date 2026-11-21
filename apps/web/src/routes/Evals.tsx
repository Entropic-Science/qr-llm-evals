import { cards, type EvalCard } from "@qr-evals/contracts";
import { Link } from "wouter";
import { Badge } from "../components/Badge";
import { Table, type TableColumn } from "../components/Table";

type RegistryCard = EvalCard & { title?: string };
const registryCards = cards as readonly RegistryCard[];
const number = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 });
const columns: readonly TableColumn<RegistryCard>[] = [
  { key: "eval", label: "Evaluation", className: "eval-column", render: (card) => <div><strong>{card.title ?? card.id}</strong><code>{card.id}</code><p>{card.purpose}</p></div> },
  { key: "status", label: "Status", render: (card) => <div className="status-stack"><Badge tone={card.status === "implemented" ? "positive" : "muted"}>{card.status}</Badge><span>{card.selectable ? "Selectable" : "Not selectable"}</span></div> },
  { key: "tier", label: "Tier", render: (card) => <span className="data-value">T{card.tier}</span> },
  { key: "k", label: "k", render: (card) => <span className="data-value">{card.k ?? "—"}</span> },
  { key: "tokens", label: "Generated tokens / arm", render: (card) => <span className="data-value" title={card.est_gen_tokens_per_arm.toLocaleString("en-US")}>{number.format(card.est_gen_tokens_per_arm)}</span> },
  { key: "endpoints", label: "Endpoints", render: (card) => { const secondary = card.endpoints.secondary ?? []; return <div className="endpoint-list"><strong>{card.endpoints.primary.join(", ")}</strong>{secondary.length ? <span>{secondary.join(", ")}</span> : null}</div>; } },
  { key: "engine", label: "Engine", render: (card) => <code>{card.engine}</code> },
];

export function Evals() {
  const implemented = registryCards.filter((card) => card.status === "implemented").length;
  const selectable = registryCards.filter((card) => card.selectable).length;
  return <div className="registry-page"><header className="page-header"><div><div className="page-heading"><p className="eyebrow">Frozen registry · contract export</p><h1>Evaluation registry</h1></div><p>Every row is generated from the same card contract used by the runner. Planned work remains visible, but cannot enter a study.</p></div><Link className="button button--secondary" href="/evals/add">Contribute an eval</Link></header><dl className="registry-summary"><div><dt>Total cards</dt><dd>{registryCards.length}</dd></div><div><dt>Implemented</dt><dd>{implemented}</dd></div><div><dt>Selectable today</dt><dd>{selectable}</dd></div></dl><div className="registry-legend"><Badge tone="positive">implemented</Badge><span>Runnable implementation exists</span><Badge tone="muted">planned</Badge><span>Visible for methodological planning</span></div><Table caption="Evaluation registry" columns={columns} rows={registryCards} rowKey={(card) => card.id} /></div>;
}
