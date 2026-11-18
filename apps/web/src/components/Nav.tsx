import { useEffect } from "react";
import { Link, useLocation } from "wouter";
import { api, ApiError } from "../lib/api";
import { clearSession, updateSessionUser, useSession } from "../lib/session";

const links = [{ href: "/", label: "Instrument" }, { href: "/evals", label: "Eval registry" }, { href: "/docs/determinism", label: "Methods" }];
export function Nav() {
  const [location] = useLocation();
  const session = useSession();
  const token = session?.token;
  // The stored account can go stale (an admin flag, a re-linked key, a revoked session): ask once per page load.
  useEffect(() => {
    if (!token) return;
    let live = true;
    api.me(token).then((user) => { if (live) updateSessionUser(user); }).catch((cause: unknown) => { if (live && cause instanceof ApiError && cause.status === 401) clearSession(); });
    return () => { live = false; };
  }, [token]);
  const accountActive = ["/account", "/login", "/signup", "/admin"].includes(location);
  return <header className="site-header"><Link className="brand" href="/" aria-label="qr-llm-evals home"><span className="brand__field" aria-hidden="true"><i /><i /><i /><i /></span><span><strong>QR</strong> / LLM EVALS</span></Link><nav aria-label="Primary navigation">{links.map(({ href, label }) => { const active = href === "/" ? location === "/" : location.startsWith(href.split("/").slice(0, 2).join("/")); return <Link key={href} href={href} className={active ? "nav-link is-active" : "nav-link"} aria-current={active ? "page" : undefined}>{label}</Link>; })}<Link href={session ? "/account" : "/login"} className={accountActive ? "nav-link nav-link--account is-active" : "nav-link nav-link--account"} aria-current={accountActive ? "page" : undefined}>{session ? session.user.username : "Sign in"}</Link></nav></header>;
}
