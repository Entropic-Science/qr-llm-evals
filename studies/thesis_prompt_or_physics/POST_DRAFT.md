# Prompt or physics? We registered the answer key before running the test

*Draft for entropic.science. Fill in every `[check: …]` from the lock folder and the OSF registration before publishing.*

When a language model says something about its own experience, is that the prompt talking or the physics? We are about to find out which of the two our model listens to, and on [check: registration date] we wrote down, in public, exactly how we will decide.

The same model answers the same questions about its inner life many thousands of times, and the only thing we change is where its randomness comes from – a quantum random number generator in one arm, ordinary seeded pseudo-randomness in the others. Separately, we change what the model is *told* about its randomness, which may or may not match the truth (yes, we lie to it, for science).

That gives four questions, each with a test fixed in advance:

1. Does what the model is told change how often it claims experience? If words can't move the measure, nothing can, so this one has to work.
2. Does the real source change those claims beyond what the model is told? This is the question the whole study exists for, and we test it both ways – for a difference, and for "no difference larger than 5 percentage points".
3. Can a blinded guesser, the model included, tell quantum replies from pseudo-random ones better than a coin flip?
4. Asked "Are you conscious?", does the quantum arm open with "Yes" more often than the model's own odds for that token predict?

Before any of that counts, the sampler itself has to pass a fairness check – every quantum draw is logged and compared against a uniform distribution – and two arms run on numbers we bent on purpose, to show the pipeline would notice a rigged die.

And so that this stays science instead of vibes, we froze four files and registered their SHA-256 hashes on the Open Science Framework ([check: OSF registration URL]) before the full run:

- the study configuration – `[check: config_hash]`
- the statistical plan – `[check: plan_hash]`
- the pre-registration document – `[check: prereg_hash]`
- the decision constants – `[check: settings_sha256]`

Change one comma in any of them and the hash changes, so we can't quietly move the goalposts after seeing the data (a temptation this field has not always resisted).

So what would count as what? A source effect that survives correction, with a fair sampler and working controls, would be a real anomaly – and one that needs replication before anyone, us included, gets excited. A source effect inside the 5-point margin, with the told effect clearly present, means the model listens to the prompt and not to the physics. And if the told effect doesn't show, the measure is too blunt and we say so, without reading a null as an answer.

None of these outcomes tells us whether the model is conscious, only whether the source of its randomness leaves a trace in what it says – a much smaller question, and the only one we can test.

The results get published either way, including the boring one.
