import { cards as registryCards, type Arm, type EvalCard, type ModelRef } from "@qr-evals/contracts";
import { useEffect, useMemo, useState } from "react";
import { useLocation } from "wouter";
import { DeterminismBriefing } from "../../components/DeterminismBriefing";
import { EstimateTotals } from "../../components/EstimateTotals";
import { LaunchDialog } from "../../components/run/LaunchDialog";
import { api } from "../../lib/api";
import { configHash, loadConfig, saveConfig, studyOneTemplate, type WizardStudyConfig } from "../../lib/config-store";
import { estimateSelection } from "../../lib/estimates";
import { Evals } from "./Evals";
import { Judge } from "./Judge";
import { Keys } from "./Keys";
import { Models } from "./Models";
import { Review } from "./Review";
import { Sampling } from "./Sampling";
import { Supervised } from "./Supervised";

export interface VolatileKeys { launch: string; anthropic: string; external: string }
export interface WizardStepProps {
  config: WizardStudyConfig;
  updateConfig: (config: WizardStudyConfig) => void;
  cards: readonly EvalCard[];
  modelOptions: ModelRef[];
  onDeterministic: () => void;
  keys: VolatileKeys;
  setKeys: (keys: VolatileKeys) => void;
  hash: string;
  launchState: string;
  onLaunch: () => void;
}

const steps = [
  ["Models", Models], ["Sampling", Sampling], ["Evals", Evals], ["Supervised", Supervised],
  ["Judge", Judge], ["Keys", Keys], ["Review", Review],
] as const;
const ACK_KEY = "qr-evals.determinism-briefing.acknowledged";

function modelRef(model: { id: string; family: string; provider: string; served_name: string; checkpoint?: string | null }): ModelRef | null {
  if ((model.family !== "qr_llm" && model.family !== "external") || !["qr_server", "openrouter", "openai", "anthropic_compat", "custom"].includes(model.provider)) return null;
  return { id: model.id, family: model.family, provider: model.provider as ModelRef["provider"], served_name: model.served_name, checkpoint: model.checkpoint ?? null };
}

export function Configure() {
  const [, navigate] = useLocation();
  const [config, setConfig] = useState(loadConfig);
  const [current, setCurrent] = useState(0);
  const [cards, setCards] = useState<readonly EvalCard[]>(registryCards);
  const [modelOptions, setModelOptions] = useState<ModelRef[]>(config.models);
  const [briefingOpen, setBriefingOpen] = useState(false);
  const [keys, setKeys] = useState<VolatileKeys>({ launch: "", anthropic: "", external: "" });
  const [hash, setHash] = useState("");
  const [launchState, setLaunchState] = useState("");
  const [launchOpen, setLaunchOpen] = useState(false);

  useEffect(() => { saveConfig(config); void configHash(config).then(setHash); }, [config]);
  useEffect(() => {
    const controller = new AbortController();
    void Promise.allSettled([api.cards(), api.models()]).then(([cardResult, modelResult]) => {
      if (controller.signal.aborted) return;
      if (cardResult.status === "fulfilled" && cardResult.value.cards.length > 0) setCards(cardResult.value.cards.map(({ card }) => card));
      if (modelResult.status === "fulfilled") {
        const models = modelResult.value.models.map(modelRef).filter((model): model is ModelRef => model !== null);
        if (models.length > 0) setModelOptions(models);
      }
    });
    return () => controller.abort();
  }, []);

  const selectedCards = useMemo(() => {
    const ids = new Set(config.cards.map(({ card_id }) => card_id));
    return cards.filter(({ id }) => ids.has(id));
  }, [cards, config.cards]);
  const overrides = useMemo(() => Object.fromEntries(config.cards.map((selection) => [selection.card_id, selection.overrides])), [config.cards]);
  const modelId = config.models[0]?.id ?? "qr-llm";
  const estimate = useMemo(() => estimateSelection(selectedCards, {
    modelId,
    tpsByModel: config.estimates?.tps_by_model ?? { [modelId]: 400 },
    readingTps: config.estimates?.reading_tps,
    tiledStreams: config.estimates?.tiled_streams,
    sessionMin: config.estimates?.session_min,
    overrides,
  }), [selectedCards, modelId, config.estimates, overrides]);
  const availableArms = config.arms.filter((arm: Arm) => arm.availability !== "unavailable").length;
  const CurrentStep = steps[current]?.[1] ?? Models;
  const canContinue = current !== 0 || config.models.length > 0;

  const showDeterminism = () => {
    if (window.sessionStorage.getItem(ACK_KEY) !== "yes") setBriefingOpen(true);
  };
  const closeBriefing = () => { window.sessionStorage.setItem(ACK_KEY, "yes"); setBriefingOpen(false); };
  const launch = () => {
    if (!keys.launch) { setLaunchState("Enter a launch key in step 06 before launching."); return; }
    setLaunchState("");
    setLaunchOpen(true);
  };
  const stepProps: WizardStepProps = { config, updateConfig: setConfig, cards, modelOptions, onDeterministic: showDeterminism, keys, setKeys, hash, launchState, onLaunch: launch };
  const providerKeys: Record<string, string> = {};
  if (keys.anthropic) providerKeys.anthropic = keys.anthropic;
  if (keys.external) providerKeys.openrouter = keys.external;

  return <div className="configure-page">
    <header className="configure-header"><div><p className="eyebrow">Study configuration · editable protocol</p><h1>Build the comparison.</h1></div><button className="button button--secondary" type="button" onClick={() => { setConfig(studyOneTemplate()); setCurrent(0); }}>Load Study 1 template</button></header>
    <div className="wizard-layout">
      <nav className="step-rail" aria-label="Configuration steps"><p className="step-rail__progress">{String(current + 1).padStart(2, "0")} / {String(steps.length).padStart(2, "0")}</p><ol>{steps.map(([label], index) => <li key={label}><button type="button" className={index === current ? "is-current" : index < current ? "is-complete" : ""} aria-current={index === current ? "step" : undefined} onClick={() => setCurrent(index)}><span>{String(index + 1).padStart(2, "0")}</span>{label}</button></li>)}</ol></nav>
      <main className="wizard-workbench"><CurrentStep {...stepProps} /><footer className="wizard-actions"><button className="button button--secondary" type="button" disabled={current === 0} onClick={() => setCurrent((value) => Math.max(0, value - 1))}>Previous</button>{current < steps.length - 1 && <button className="button button--primary" type="button" disabled={!canContinue} onClick={() => setCurrent((value) => Math.min(steps.length - 1, value + 1))}>Continue</button>}</footer></main>
      <EstimateTotals estimate={estimate} armCount={availableArms} supervisedDisplay={config.supervised?.display ?? "single"} />
    </div>
    <DeterminismBriefing open={briefingOpen} onClose={closeBriefing} />
    <LaunchDialog open={launchOpen} config={config} launchKey={keys.launch} providerKeys={providerKeys} onClose={() => setLaunchOpen(false)} onClearKeys={() => setKeys({ launch: "", anthropic: "", external: "" })} onLaunched={(runId) => navigate(`/runs/${encodeURIComponent(runId)}`)} />
  </div>;
}
