import { useState } from "react";

export function Briefing({ onConsent, join = false }: { onConsent: () => void; join?: boolean }) {
  const [checked, setChecked] = useState(false);
  return <section className="briefing notebook-page" aria-labelledby="briefing-title">
    <div className="page-heading"><p className="eyebrow">Before you begin</p><h1 id="briefing-title">Observe without presuming.</h1></div>
    <div className="briefing__copy">
      <p>This mode studies whether a person's state of consciousness, real-time observation, or other correlates are associated with the behaviour of a system that uses quantum randomness. One example asks whether an observer's sustained intention might be reflected in the semantic direction of the generated text. <strong>That is an open research question, not a promised effect.</strong></p>
      <p>You will choose an intention, pause with it for a few minutes, and watch prompts and model output arrive. A target theme may also be drawn and shown to observers; it is never included in a model prompt. We record your pseudonymous observer ID, questionnaire answers, presence during each window, intention text and timing, and the generated study artifacts. The host cannot start a session until the required pre-session questions are complete.</p>
      <p>The countdowns are gentle guides, not commands. They may pass zero and continue into negative time. <strong>Nothing starts automatically:</strong> the host must always choose Start or Resume. You may step away or stop participating at any time.</p>
      <p>When you are ready, take one conscious breath. There is no need to strain or to produce a particular experience. Lots of love and kindness.</p>
    </div>
    <label className="consent-line"><input type="checkbox" checked={checked} onChange={(event) => setChecked(event.target.checked)} /><span><strong>I understand and consent</strong><small>By choosing I consent and join, I confirm that I understand the description above. I agree that my pseudonymous observer ID, session presence, intention and questionnaire responses may be stored with the research run and analysed under the study's published data-sharing terms. I understand that free-text responses may contain identifying details, so I will avoid including information I do not want shared. Participation is voluntary, I may leave at any time, and leaving does not remove paid-for run artifacts; a withdrawal request will instead mark the affected data according to the published study protocol.</small></span></label>
    <button className="button button--primary" type="button" disabled={!checked} onClick={onConsent}>{join ? "Continue to join" : "Continue to intention"}</button>
  </section>;
}
