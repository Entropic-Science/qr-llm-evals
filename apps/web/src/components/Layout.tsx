import type { PropsWithChildren } from "react";
import { Logo } from "./Logo";
import { Nav } from "./Nav";

const footerLinks = [
  { label: "entropic.science", href: "https://entropic.science" },
  { label: "GitHub", href: "https://github.com/Entropic-Science/qr-llm-evals" },
  { label: "Discord", href: "https://discord.gg/2EbveaB2wS" },
  { label: "X", href: "https://x.com/EntropicScience" },
  { label: "Charter", href: "https://github.com/Entropic-Science/community/blob/main/CHARTER.md" },
];

// Film grain over the whole page, as on entropic.science: one fixed SVG turbulence layer, multiplied at 4%.
function NoiseOverlay() {
  return <svg className="noise-overlay" aria-hidden="true"><filter id="noise-filter"><feTurbulence type="fractalNoise" baseFrequency="0.8" numOctaves="3" stitchTiles="stitch" /></filter><rect width="100%" height="100%" filter="url(#noise-filter)" /></svg>;
}

export function Layout({ children }: PropsWithChildren) {
  return <div className="app-shell">
    <a className="skip-link" href="#main-content">Skip to content</a>
    <NoiseOverlay />
    <Nav />
    <main id="main-content" className="page-shell">{children}</main>
    <footer className="site-footer">
      <div className="site-footer__inner">
        <div className="site-footer__row">
          <Logo size={30} tag="evals" />
          <nav aria-label="Footer">{footerLinks.map(({ label, href }) => <a key={href} href={href} target="_blank" rel="noreferrer noopener">{label}</a>)}</nav>
        </div>
        <p className="site-footer__note">© {new Date().getFullYear()} Entropic Science. An open research collective building quantum-random AI. <span>Open methods, inspectable provenance.</span></p>
      </div>
    </footer>
  </div>;
}
