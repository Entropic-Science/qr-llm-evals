import { CONTRACTS_VERSION } from "@qr-evals/contracts";

export function App() {
  return (
    <main className="mx-auto max-w-2xl p-8 font-sans text-neutral-900">
      <h1 className="text-2xl font-semibold">qr-llm-evals</h1>
      <p className="mt-2 text-neutral-600">
        Pre-registered research harness — quantum-random vs pseudo-random token sampling.
      </p>
      <p className="mt-4 text-xs text-neutral-400">contracts {CONTRACTS_VERSION}</p>
    </main>
  );
}
