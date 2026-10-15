import { Timer } from "./Timer";
export function BreakScreen({ remaining }: { remaining: number }) { return <section className="phase-screen phase-screen--break"><p className="eyebrow">Intermission</p><h2>Let the observation rest.</h2><Timer remaining={remaining} label="Break guide" /><p>Expiry does not resume generation. The host must end the break explicitly.</p></section>; }
