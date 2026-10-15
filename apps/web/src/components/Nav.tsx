import { Link, useLocation } from "wouter";

const links = [{ href: "/", label: "Instrument" }, { href: "/evals", label: "Eval registry" }, { href: "/docs/determinism", label: "Methods" }];
export function Nav() {
  const [location] = useLocation();
  return <header className="site-header"><Link className="brand" href="/" aria-label="qr-llm-evals home"><span className="brand__field" aria-hidden="true"><i /><i /><i /><i /></span><span><strong>QR</strong> / LLM EVALS</span></Link><nav aria-label="Primary navigation">{links.map(({ href, label }) => { const active = href === "/" ? location === "/" : location.startsWith(href.split("/").slice(0, 2).join("/")); return <Link key={href} href={href} className={active ? "nav-link is-active" : "nav-link"} aria-current={active ? "page" : undefined}>{label}</Link>; })}</nav></header>;
}
