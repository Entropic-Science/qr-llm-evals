# Prompt or physics – everything is wired, here is how to drive it

*For hdolivaw, from Alchy. State as of 21 November 2026. The step-by-step reference is still `README.md` in this folder; this is the map that tells you which parts of it now run themselves.*

Every blocker from your notes is gone, and the study can now be run end to end by one person with a terminal, an account and an Anthropic key. Below is what changed, what the box looks like right now, the exact sequence I would follow, and the three or four places where it can still bite you.

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
- The dry run (`thesis_dry_run.py`) still passes on the new log format, and it is the fastest way to see every output file before spending real entropy. Its verdict on fake data means nothing, of course.

And if something in here turns out to be wrong, it's the doc that's wrong, not you – tell me and we fix the doc.
