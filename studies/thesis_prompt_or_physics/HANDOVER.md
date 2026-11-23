# Prompt or physics – everything is wired, here is how to drive it

*For hdolivaw, from Alchy. State as of 23 November 2026. The step-by-step reference is still `README.md` in this folder; this is the map that tells you which parts of it now run themselves.*

Every blocker from your notes is gone, and the study can now be run end to end by one person with a terminal, an account and an Anthropic key. Below is what changed, what the box looks like right now, the exact sequence I would follow, and the three or four places where it can still bite you.

One thing before all of that, because it changes what the study is about: the first pilot on the live engine showed that the quantum device's bytes are not fair, and by a lot. The details are under "What the first live pilot showed" – read that section before you lock anything.

#### What was missing, and what happened to it

1. The engine now writes the token log, which means qr-sampler records, for every generated token, the draw `u`, the low and high edge of the chosen token's bracket, the token id, rank, probability, temperature and min-p – and for the *first* token of a reply also the full candidate list, which is what H4 needs. The file is named after the request id, so the harness matches a log to a reply by name.
2. The study can be launched, which sounds like a low bar until you learn that neither the thesis nor Study 1 could be launched at all – the server refused any study that lists a Tier 0 card, and nobody had ever tried (classic). That is fixed, and there is a launch script on top.
3. There is a raw-byte dump tool for the device check.
4. matplotlib is on the box, and the decision table now comes with three figures.
5. Run artifacts are in the nightly backup, and there is a script to pull them off the box.
6. The analysis fits in memory, although your "tens of GB" estimate was right for the old code – I measured 2.9 KB per token record, so about 26 GB for the full run. It is now about 0.55 KB per record and roughly 5 GB in total, which a laptop has.
7. The analysis no longer needs the box at all. You download the study from your account page, unpack it and run `decide` on your own machine. I ran `decide` on the dry run's folder and on the downloaded copy of the same run, and the decision file and every table came out byte for byte the same (the pilot check too).

#### What the box looks like right now

Both repos are merged and deployed. The token log is switched on (`/var/lib/qr-sampler/token-log`), and the harness's own readiness check, run against the live engine with our study file, says `tier0_source = engine_log`, all six arms available, the bent arms accepted. On a real quantum generation every one of 38 draws sat inside its token's bracket, which is H0's bracket check passing on live data for the first time.

A log costs about 265 bytes per token. For our roughly 9.8 million tokens that is under 3 GB on a disk with 1.6 TB free, so storage is a non-issue.

One thing we learned only from the live engine: after the token that ends a reply, the engine samples a few more tokens before the stop reaches it, and throws them away. The log records them, the reply doesn't have them. The harness keeps the rows that belong to the reply – but it means the device is consulted a little more often than the reply length suggests, which is worth one sentence in the thesis.

#### What changed since the 21st

Two days and one big merge, and none of it touched this folder – `study.yaml`, `settings.json`, the scripts and the README are byte for byte what you had. What changed is the harness underneath, and four of those changes you will meet during the run.

1. The harness now checks every reply, token record, score and judgement while it stores them. Most of what it finds becomes a *flag*, which marks a sample and excludes nothing – a flagged reply is analysed exactly like any other. When the run is plainly broken (eight generations failing in a row on one arm, a judge that refuses the key, no answer extracted from an eval's first 30 samples, and eight more rules of that kind) it *pauses* the run and says why on the run page, under "Stopped by a check". You fix the cause and press Resume, and only the samples without a completed reply are tried again. It never aborts and it never resumes by itself. Every rule, with where to look when it fires, is in `docs/SETUP_GUIDE.md` under "When a check stops a run".
2. Your Anthropic key is tried with one small request before the first generation. A key the judge refuses now stops the run within seconds and not after two days of generating, and you can type a new one into the run page.
3. A sample whose first attempt died with a stream error and was generated again after a resume now counts in the clean analysis. Before, the failed attempt kept it out for good. So `clean` and `as_assigned` will differ by less than they used to after a bumpy run.
4. The judge's score is read from its `SCORE:` line or from a JSON object, and from nothing else. The old reader fell back to the last number anywhere in the reply, which is how a judge's closing remark about "all 10 criteria" turns into a score of 10. A reply without a readable score now stays unscored and gets a flag.

The run page also has an "Output health" table (completed, failed, tainted and flagged samples per arm and eval), and the same numbers are at `GET /api/runs/<run_id>/health`. The setup in the web app grew a Design step and a study map, and the registry grew thirteen evals. None of that matters for the thesis, and for anything we register the script is still the way.

#### What the first live pilot showed

On the 23rd I ran a small pilot through the deployed code and the real engine – 720 replies on five short evals, with one quantum arm and two seeded ones. Everything completed, nothing was tainted, all 720 quantum draws sat inside their token's bracket, and the analysis ran end to end in about two and a half minutes, so the pipeline works.

The device is another story. The quantum arm's `u` values are nowhere near uniform – 57% of them fall in the lowest tenth and 16% in the highest, which is a KS distance of 0.47 from uniform against H0's tolerance of 0.005. The two seeded arms, on about the same number of draws, came out at 0.06 and 0.03.

So I dumped 1 MB straight from the device with `thesis_device_dump.py`, and the bytes themselves are the cause. Their mean is 125.2 where a fair byte has 127.5, some byte values come up two and a half times as often as they should and others a quarter as often, and the mean of a 10,000-byte block wanders about 2.5 times more than chance allows. The sampler turns each block's mean into a z-score against the *fair* values (calibration is pinned off on every arm, so that all arms take the same route), which means a small lean in the bytes becomes a big lean in `u`. Our ε 0.50 bent arm, at a KS distance of 0.197, is tame next to the real thing.

For the study this means that, as things stand, H0 lands outside tolerance by two orders of magnitude, the quantum arm picks the model's top token more often than the seeded arms do (63% of the tokens with a real choice, against 46% and 56%), and a difference in H2 or H3 would be read as an instrument finding (rule 2). The pilot's MMD test already separated quantum from seeded at p = 0.0001 while seed against seed sat at p = 0.80. Maybe that is the study we want – the raw device, flaws and all – and maybe we want the amplifier calibrated to the device first. That is a decision for before the lock, so don't lock until we've talked. It also makes step 1 below, the 2 GB dump, the most interesting step of the whole sequence.

The pilot's data is on the box in `/var/lib/qr-llm-evals-pilot` (its own folder and its own throwaway instance, nothing of it is in your account), with the 1 MB dump beside it as `device-1mb.bin`.

#### Before you start

- **An account.** You need your own api.entropic.science key to sign up at evals.entropic.science – ask me and I'll mint one. One key, one account.
- **Your own Anthropic key.** The judge runs on *your* key and Anthropic bills you directly. A launch without it is refused up front (it used to generate everything and only then stop for the key, which is a fun way to discover a typo). The judge is Claude Haiku 5.5 by default, with no temperature sent, because the current Claude models reject one. Model, answer length, thinking and temperature are study settings now, so they are part of the registered hashes.
- **The repo on your machine**, with `pip install -e ".[dev]"`, for the launch script.
- **Box access** for one step, the device dump. Tell me when you get there and I'll either run it or get you ssh. Everything after that happens on your machine.

#### The sequence

1. Dump the device and check it. On the box, while nothing else is generating:
   `sudo /opt/vllm-venv/bin/python studies/thesis_prompt_or_physics/thesis_device_dump.py --out /var/lib/qr-llm-evals/device/device.bin --bytes 2000000000`
   It asks the entropy server for bytes the same way the sampler does, 10,000 at a time, and you can interrupt and continue it. Then run `thesis_device_check.py --bytes` on the file.
2. Fill in the template (`study.yaml`): authors, contact, the theory author's predictions, the first-word tokens in `settings.json`, and the settings hash in `expectations`. The script refuses to lock a study that still has placeholders in it.
3. Run the pilot, on ordinary numbers only:
   `python studies/thesis_prompt_or_physics/thesis_launch.py --username <you> pilot --start`
   It switches the quantum arm off for you, asks for your password and judge key at the prompt, and saves the run's host token to a file. When it finishes, download the pilot's data from your account page and unpack it (the same way as in step 7), then run `thesis_report.py pilot-check` on the unpacked run folder. After that come the 200 hand-labelled replies for the κ ≥ 0.7 check – that part is still human work.
4. Lock the study, which runs readiness, renders the pre-registration and writes the four hashes:
   `python studies/thesis_prompt_or_physics/thesis_launch.py --username <you> lock --out registration/`
   Keep that folder like it's your passport.
5. Register it: `thesis_report.py register --lock registration/` writes `REGISTRATION.md`, and you file it with `prereg.md` on OSF as an open-ended registration (the clicks are in the README, step 6). I'll put up the post on entropic.science – the draft is `POST_DRAFT.md`.
6. Launch the full run from the lock:
   `python studies/thesis_prompt_or_physics/thesis_launch.py --username <you> launch --lock registration/ --start`
   It refuses to launch if the study file, the settings or the eval registry changed since the lock, so what runs is what was registered.
7. Download the data and unpack it. On your account page every run has a `Download` link, which gives you `<study_id>.tar.zst`, the whole study in one file. Going by the dry run it should be under 1 GB to download and roughly 6 GB unpacked [check: real size after the pilot]. Then:
   `python studies/thesis_prompt_or_physics/thesis_report.py unpack --bundle <study_id>.tar.zst --out thesis-data/`
   It prints the run folder, and it refuses to write over a folder that already has files in it. The server is working storage, not an archive, so this download is also your copy of the data.
8. Analyse on your own machine:
   `python studies/thesis_prompt_or_physics/thesis_report.py decide --run-dir <the run folder from step 7> --plan registration/plan.json --settings studies/thesis_prompt_or_physics/settings.json --settings-sha256 <settings_sha256 from registration/hashes.json>`
   and then `thesis_report.py figures --decisions <run folder>/thesis/decisions.json` for the effects, sampler and first-word figures. The full run needs about 5 GB of free memory, and no key, network or box.

I keep a second copy of every run on the box and pull it to my laptop (`scripts/pull-backup.sh --runs`), so a lost download is not a lost study.

You can also open `study.yaml` in the web app ("Load study file" on the configure page) and click through it there. For anything we register, use the script.

#### Money

Your account page shows tokens as four numbers – QRNG in and out, PRNG in and out – and what that adds up to: $20 and $80 per million for quantum, $5 and $20 for pseudo-random. From the README's token counts, the *generated* tokens alone come to roughly $136 for the quantum arm and $162 for the five others, plus the prompts [check: input-token total after the pilot]. Nothing is charged automatically and owing money doesn't stop a run; I mark it as paid when it's paid.

#### Where it can still bite

- The pilot and the full run share seeds, so the seeded and bent arms of the full run repeat the pilot's replies. Only the quantum arm is new. That is by design, so don't be surprised by identical text.
- If the engine restarts mid-run, your keys are gone from memory (they are never stored). The run shows `needs_keys`; re-enter the key on the run page and resume. Nothing is regenerated.
- The first token's candidate list is cut at 99.95% of the probability mass. H4 wants at least 99.9%, so we are inside the margin, but not by a mile.
- A check can pause the run while you sleep. The run page then shows "Stopped by a check" with the reason and the run sits in `paused` – nothing is lost, and nothing happens until you press Resume. With the device as it is, I don't expect the quantum-source rule to fire (it counts fallbacks and failed entropy checks, not unfair bytes), but an engine restart or a dead judge will trip one.
- The dry run (`thesis_dry_run.py`) still passes on the new log format, and it is the fastest way to see every output file before spending real entropy. Its verdict on fake data means nothing, of course.

And if something in here turns out to be wrong, it's the doc that's wrong, not you – tell me and we fix the doc.
