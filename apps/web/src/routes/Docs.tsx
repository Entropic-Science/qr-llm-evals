import { Link, useLocation } from "wouter";
import { Markdown } from "../components/Markdown";
import { getDoc, getDocSlugs } from "../lib/markdown";

const labels: Record<string, string> = { determinism: "Determinism", DESIGN: "System design", evals: "Eval registry", "add-an-eval": "Adding an eval", API: "API", ANALYSIS_PLAN: "Analysis plan", SETUP_GUIDE: "Setup guide" };

export function Docs() {
  const [location] = useLocation();
  const requested = decodeURIComponent(location.replace(/^\/docs\/?/, ""));
  const available = getDocSlugs();
  const slug = requested || (available.includes("DESIGN") ? "DESIGN" : available[0]);
  const source = slug ? getDoc(slug) : undefined;
  if (!source) return <section className="empty-state"><p className="eyebrow">Documentation</p><h1>Document not found</h1><p>The requested research note is not part of this build.</p><Link className="text-link" href="/docs">Browse available docs →</Link></section>;
  return <div className="docs-layout"><aside className="docs-index"><p className="eyebrow">Research notes</p><nav aria-label="Documentation">{available.filter((item) => labels[item]).map((item) => <Link key={item} href={`/docs/${item}`} className={item === slug ? "docs-link is-active" : "docs-link"}>{labels[item] ?? item}</Link>)}</nav></aside><Markdown source={source} /></div>;
}
