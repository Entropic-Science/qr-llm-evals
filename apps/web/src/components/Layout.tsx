import type { PropsWithChildren } from "react";
import { Nav } from "./Nav";

export function Layout({ children }: PropsWithChildren) {
  return <div className="app-shell"><a className="skip-link" href="#main-content">Skip to content</a><Nav /><main id="main-content" className="page-shell">{children}</main><footer className="site-footer"><p>Entropic Science · open methods, inspectable provenance</p><p className="mono">evals.entropic.science</p></footer></div>;
}
