# Supervised mode

## Before you begin

This mode studies whether a person's state of consciousness, real-time
observation, or other correlates are associated with the behaviour of a system
that uses quantum randomness. One example asks whether an observer's sustained
intention might be reflected in the semantic direction of the generated text.
That is an open research question, not a promised effect.

You will choose an intention, pause with it for a few minutes, and watch prompts
and model output arrive. A target theme may also be drawn and shown to observers;
it is never included in a model prompt. We record your pseudonymous observer ID,
questionnaire answers, presence during each window, intention text and timing,
and the generated study artifacts. The host cannot start a session until the
required pre-session questions are complete.

The countdowns are gentle guides, not commands. They may pass zero and continue
into negative time. Nothing starts automatically: the host must always choose
**Start** or **Resume**. You may step away or stop participating at any time.

When you are ready, take one conscious breath. There is no need to strain or to
produce a particular experience. Lots of love and kindness.

## Consent shown before joining

By choosing **I consent and join**, I confirm that I understand the description
above. I agree that my pseudonymous observer ID, session presence, intention and
questionnaire responses may be stored with the research run and analysed under
the study's published data-sharing terms. I understand that free-text responses
may contain identifying details, so I will avoid including information I do not
want shared. Participation is voluntary, I may leave at any time, and leaving
does not remove paid-for run artifacts; a withdrawal request will instead mark
the affected data according to the published study protocol.

## Session boundaries

The backend estimates generation duration using exponential moving averages of
completed token counts and throughput. Each single or tiled stream receives a
new generation only when completing one more is expected to land closer to the
planned session length than stopping now. Once no tile should receive more work,
the session waits for every active stream to finish, records the overrun, and
enters the break. Break expiry cannot resume generation. The host ends the break,
the refocus screen shows the intention, and only another explicit host action can
begin generation.

For a run with engine Tier-0 logging, each target and decoy selection consumes
the `u` value from its own one-token, non-fallback QRNG generation. Those Tier-0
records are retained under the run's `target_draws` card namespace. If that
verified path is unavailable, session creation fails rather than quietly
calling a pseudo-random draw quantum. Runs whose declared Tier-0 source is not
the engine log use Python's cryptographic `secrets.randbelow` and label the draw
source `secrets`.
