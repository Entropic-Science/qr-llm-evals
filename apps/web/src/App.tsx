import { Link, Route, Switch } from "wouter";

import { Study } from "./routes/studies/Study";
import { StudyPrereg } from "./routes/studies/StudyPrereg";
import { StudyData } from "./routes/studies/StudyData";

import { Layout } from "./components/Layout";
import { Docs } from "./routes/Docs";
import { Evals } from "./routes/Evals";
import { EvalsAdd } from "./routes/EvalsAdd";
import { NotFound } from "./routes/NotFound";
import { Prereg } from "./routes/Prereg";
import { Run } from "./routes/Run";
import { Join } from "./routes/Join";
import { Live } from "./routes/Live";
import { Configure } from "./routes/configure";

function Home() {
  return <div className="home-stack">
    <section className="hero" aria-labelledby="home-title">
      <p className="eyebrow">Pre-registered research instrument · v0.1</p>
      <h1 id="home-title">Trace the difference back to its source.</h1>
      <p className="hero__lede">Compare quantum-random and seeded pseudo-random token sampling on one controlled evaluation pipeline—with the study design, provenance, and analysis fixed in advance.</p>
      <div className="hero__actions"><Link className="button button--primary" href="/evals">Inspect the eval registry</Link><Link className="text-link" href="/docs/determinism">Read the determinism briefing <span aria-hidden="true">→</span></Link></div>
    </section>
    <section className="instrument" aria-labelledby="instrument-title">
      <div><p className="eyebrow">Study configuration</p><h2 id="instrument-title">The run builder lands in the next stage.</h2><p>This shell establishes the public registry, research documentation, and typed connection to the run service. No study starts from this page yet.</p></div>
      <dl className="instrument__readout"><div><dt>Experimental source</dt><dd><span className="source-mark source-mark--quantum" />QRNG</dd></div><div><dt>Control source</dt><dd><span className="source-mark source-mark--control" />Seeded PRNG</dd></div><div><dt>Analysis</dt><dd>Pre-registered</dd></div></dl>
    </section>
  </div>;
}

export function App() {
  return <Layout><Switch><Route path="/" component={Configure} /><Route path="/home" component={Home} /><Route path="/prereg" component={Prereg} /><Route path="/runs/:runId/live" component={Live} /><Route path="/runs/:runId" component={Run} /><Route path="/s/:joinCode" component={Join} /><Route path="/studies/:studyId/prereg" component={StudyPrereg} /><Route path="/studies/:studyId/data" component={StudyData} /><Route path="/studies/:studyId" component={Study} /><Route path="/evals/add" component={EvalsAdd} /><Route path="/evals" component={Evals} /><Route path="/docs/:rest*" component={Docs} /><Route path="/docs" component={Docs} /><Route component={NotFound} /></Switch></Layout>;
}
