import { useEffect } from "react";
import { Link, useLocation } from "wouter";
import { api, ApiError } from "../lib/api";
import { InkLine } from "./InkLine";
import { Logo } from "./Logo";
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
  const accountLabel = session ? session.user.username : "Sign in";
  return <header className="site-header"><div className="site-header__inner">
    <Link className="brand" href="/" aria-label="qr-llm-evals home"><Logo tag="evals" /></Link>
    <nav aria-label="Primary navigation">
      {links.map(({ href, label }) => { const active = href === "/" ? location === "/" : location.startsWith(href.split("/").slice(0, 2).join("/")); return <Link key={href} href={href} className={active ? "nav-link is-active" : "nav-link"} aria-current={active ? "page" : undefined}>{label}</Link>; })}
      <Link href={session ? "/account" : "/login"} className={accountActive ? "nav-cta is-active" : "nav-cta"} aria-current={accountActive ? "page" : undefined}><span className="nav-cta__label">{accountLabel}</span><InkLine label={accountLabel} /></Link>
    </nav>
  </div></header>;
}
