import type { ModelRef } from "@qr-evals/contracts";
import type { WizardStepProps } from "./index";

const fallback: ModelRef = { id: "qr-llm", family: "qr_llm", provider: "qr_server", served_name: "qr-llm", checkpoint: null };

export function Models({ config, updateConfig, modelOptions }: WizardStepProps) {
  const options = modelOptions.length > 0 ? modelOptions : [fallback];
  return <section aria-labelledby="step-models">
    <p className="eyebrow">Protocol 01</p><h2 id="step-models">Target models</h2>
    <p className="step-intro">Select the systems under test. Only the Entropic Science qr-llm lane offers fresh quantum entropy.</p>
    <fieldset className="protocol-options"><legend className="sr-only">Models</legend>
      {options.map((model) => {
        const selected = config.models.some(({ id }) => id === model.id);
        return <label className="protocol-choice" key={model.id}>
          <input type="checkbox" checked={selected} onChange={() => updateConfig({ ...config, models: selected ? config.models.filter(({ id }) => id !== model.id) : [...config.models, model] })} />
          <span><strong>{model.served_name}</strong><small>{model.family === "qr_llm" ? "Quantum-ready · pinned envelope" : "Provider PRNG (opaque)"}</small></span>
        </label>;
      })}
    </fieldset>
    {config.models.length === 0 && <p className="validation-note" role="alert">Select at least one target model to continue.</p>}
  </section>;
}
