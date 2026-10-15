import { useState } from "react";
import { Markdown } from "../components/Markdown";
import { getDoc } from "../lib/markdown";

const contributionBrief = getDoc("add-an-eval") ?? "# Add an eval\n\nContribution instructions are unavailable in this build.";
export function EvalsAdd() {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">("idle");
  async function copyBrief() {
    try { await navigator.clipboard.writeText(contributionBrief); setCopyState("copied"); }
    catch { setCopyState("error"); }
  }
  return <div className="contribution-page"><header className="page-header page-header--rule"><div><p className="eyebrow">Open registry</p><h1>Bring a benchmark into the study.</h1><p>The document below is designed to be handed directly to a coding agent together with the repository conventions.</p></div><div className="copy-control"><button className="button button--primary" type="button" onClick={() => void copyBrief()}>Copy agent brief</button><span className="copy-status" role="status">{copyState === "copied" ? "Copied to clipboard." : copyState === "error" ? "Clipboard blocked—select the text below." : "Includes schema, tests, and PR checklist."}</span></div></header><Markdown source={contributionBrief} /></div>;
}
