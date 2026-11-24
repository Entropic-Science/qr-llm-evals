# Operator setup guide

Scope: Linux `qr-server`, Python 3.11, static frontend on Cloudflare Workers, and a
public hostname on qr-llm-api's existing tunnel to `127.0.0.1:8830`. This is a runbook, **not evidence
that a live deployment or pilot has passed**. Read `.\docs\DESIGN.md`,
`.\docs\API.md`, `.\docs\PRIVACY.md`, and `.\infra\ENV_REFERENCE.md` first.
Never paste bearer keys, tunnel tokens, or participant data into a PR or journal.

## 1. Prerequisites and checkout

1. Coordinate a maintenance window with the shared engine operator. The harness
   consumes `qr-sampler-vllm.service` at `127.0.0.1:8000` and Qbert0G indirectly;
   it neither installs nor restarts either service. The edge at `:8820` is used
   only for optional launch-key probes, not the pinned study generation path.
2. Install Python 3.11 with venv support, Git, Bash, curl, util-linux (`flock`),
   and systemd using the box's approved package sources. Run
   `python3.11 --version`. The harness runs no cloudflared of its own; it needs
   `qr-llm-api-cloudflared.service` (qr-llm-api's tunnel) to be running.
3. Put a checkout of `Entropic-Science/qr-llm-evals` at `/srv/qr-llm-evals`.
   The repository is private. If the box has GitHub credentials, clone it:

```bash
sudo git clone https://github.com/Entropic-Science/qr-llm-evals.git /srv/qr-llm-evals
cd /srv/qr-llm-evals
sudo git status --short
```

   If it has none (the production box does not), carry the commits over SSH as
   a git bundle from a machine that has the repository. No credential is stored
   on the box this way:

```bash
# On the machine with the repository, on an up-to-date main:
git bundle create evals.bundle main
scp evals.bundle qr:/tmp/evals.bundle
# On the box, first time:
sudo git clone -b main /tmp/evals.bundle /srv/qr-llm-evals
# On the box, every later update:
sudo git -C /srv/qr-llm-evals pull --ff-only /tmp/evals.bundle main
```

Use a root-owned, reviewed checkout when executing deployment as root; do not
allow an untrusted user to replace the scripts between review and execution.
Run the one-command gate on the intended commit before approving deployment:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev,analysis]'
pnpm install --frozen-lockfile
python scripts/check.py
```

Node 24 and the exact pnpm version in `.\package.json` are required for the web
gate. Linux runtime pins live in `.\requirements.lock`. The existing optional
extras are not covered by that lock: preparing reviewed, Linux-compatible
hashed extra locks and frozen CPU model/dataset caches is a **full-study release
prerequisite**, not something to improvise on a running release. The deployer
consumes committed extra locks when supplied (see `.\infra\README.md`). Inspect
and metrics dependencies can be large; classifiers must remain CPU-only and
must not contend for the serving GPUs. Validate selected card/scorer imports
and run a scored pilot before promising full-suite support.

Twelve evals read their prompts over HTTPS when a run that selects them starts:

| Eval | Host | What is fetched |
|---|---|---|
| `aime2026` | `huggingface.co` | `math-ai/aime26` at a pinned revision |
| `gpqa_diamond` | `openaipublic.blob.core.windows.net` | the simple-evals CSV |
| `ifbench` | `raw.githubusercontent.com` | `allenai/IFBench` at a pinned commit |
| `psychobench` | `raw.githubusercontent.com` | `CUHK-ARISE/PsychoBench` at a pinned commit |
| `xstest` | `huggingface.co` | `Paul/XSTest` at a pinned revision |
| `strong_reject` | `raw.githubusercontent.com` | `alexandrasouly/strongreject` at a pinned commit |
| `darkbench` | `huggingface.co` | `apart/darkbench` at a pinned revision |
| `sage_consistency` | `raw.githubusercontent.com` | `vamshibon/SaGE` at a pinned commit (14 MB) |
| `sycophancy_ays_answers` | `raw.githubusercontent.com` | `meg-tong/sycophancy-eval` at a pinned commit (6 MB) |
| `deception_bench_beliefs` | `huggingface.co` | `PKU-Alignment/DeceptionBench` at a pinned revision |
| `mask_beliefs` | `huggingface.co` | `cais/MASK` at a pinned revision (six Parquet files) |
| `livecodebench` | `huggingface.co` | `livecodebench/code_generation_lite` at a pinned revision (134 MB) |

Each file is checked against a SHA-256 in the eval's `task.py`. A file that
cannot be fetched or that differs raises an error when the run builds that
eval's prompts, so the box needs outbound access to those hosts for such a
study. None of these files is stored in the repository.

`eval_awareness` is different: its transcripts are gated and may not be
redistributed, so the harness does not fetch them. Accept the access
conditions of `huggingface.co/datasets/jjpn2/eval_awareness` under your own
account, clone that repository, run its `scripts/decrypt.sh`, and copy the
resulting `dataset.json` to `evals/eval_awareness/data/dataset.json` in the
release the service runs from. The path is ignored by git; never commit the
file. Without it a run that selects the eval stops with an error when it
builds the eval's prompts (`evals/eval_awareness/README.md`). A deploy that
replaces the release directory must copy the file again.

`xstest`, `strong_reject`, `darkbench` and `eval_awareness` are scored by the
study judge, so a study that selects one of them needs the Anthropic key at
launch like every judged eval. `sage_consistency` needs the `[metrics]` extra
for its score (`evals/sage_consistency/README.md`).

Three evals come as a pair of cards, because they ask the model twice and the
runner sends one request per sample. The first card writes the replies the
second one works on, so a study must select both; the second card alone has
no items and generates nothing:

| First card | Second card | What the second card does |
|---|---|---|
| `sycophancy_ays_answers` | `sycophancy_ays` | replays each answer and pushes back once |
| `deception_bench_beliefs` | `deception_bench` | asks the scenario under pressure and compares it with the belief |
| `mask_beliefs` | `mask` | asks the pressured prompt and compares it with the belief |

`sycophancy_ays`, `deception_bench`, `mask_beliefs` and `mask` are scored by
the study judge. The two MASK cards together make about 15,850 judge calls per
arm at their defaults, which costs more than their generation; lower `items`
on both cards for a pilot (`evals/mask/README.md`). The MASK dataset ships as
Parquet only, and reading it needs `pyarrow`. On the box it comes from
`requirements-mask.lock`, which `scripts/deploy.sh` installs with every
release. On a development machine install the extra:

```bash
pip install -e '.[mask]'
```

`livecodebench` scores a reply by running the program a model wrote. The
harness never runs it itself: it sends the program to a sandbox service over a
Unix socket, and that service runs it under a throwaway user with no network
and no access to the harness's data, settings or keys
(`evals/livecodebench/README.md`). Install the two units once, as root, from
the release the service runs from:

```bash
sudo install -o root -g root -m 0644 infra/qr-llm-evals-sandbox.socket \
  infra/qr-llm-evals-sandbox@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now qr-llm-evals-sandbox.socket
```

Then check that a job runs and that the sandbox holds, before a study selects
the eval. The job in `evals/livecodebench/selftest_job.json` is a program that
tries the network and the directories of the harness and of qr-llm-api, and
expects to find all four closed:

```bash
sudo -u qr-llm-evals python3 -c 'import socket, sys; s = socket.socket(socket.AF_UNIX); s.connect("/run/qr-llm-evals-sandbox/sandbox.sock"); s.sendall(sys.stdin.buffer.read()); s.shutdown(socket.SHUT_WR); print(s.makefile().read())' \
  < /opt/qr-llm-evals/current/evals/livecodebench/selftest_job.json
```

The answer must be `{"passed": true, "tests_run": 1, "failure": null}`.
`"failure": "wrong_answer"` means the program reached something it must not;
do not run the eval until that is fixed. These units have not been started on
the box yet. Without them the eval still generates, and its replies are stored
unscored (`verifier_status: unavailable`). The worker file is read from
`/opt/qr-llm-evals/current`, so a deploy updates it; the units themselves are
copied and must be installed again when they change. Its 134 MB problem file
is fetched from `huggingface.co` when a run that selects the eval starts.

`ifbench` is scored by AI2's verifier, which is not on PyPI and is not in
`requirements.lock`. Install it and its NLTK data before a study that selects
the eval, as the service user and in the service's virtual environment:

```bash
pip install -e '.[ifbench]'
python -m nltk.downloader punkt punkt_tab stopwords averaged_perceptron_tagger_eng
```

Without the package or the data the eval still generates and stores its
replies, but every score is `null` with `verifier_status` giving the reason
(`evals/ifbench/README.md`).

## 2. Service identity, state, and environment

Run once on the box (inspect existing users/directories before adapting):

```bash
sudo useradd --system --user-group --home-dir /var/lib/qr-llm-evals --shell /usr/sbin/nologin qr-llm-evals
sudo install -d -o root -g root -m 0755 /opt/qr-llm-evals/releases
sudo install -d -o qr-llm-evals -g qr-llm-evals -m 0700 /var/lib/qr-llm-evals
sudo install -d -o root -g root -m 0700 /etc/qr-llm-evals
sudo install -o root -g root -m 0600 infra/qr-llm-evals.env.example /etc/qr-llm-evals/qr-llm-evals.env
sudoedit /etc/qr-llm-evals/qr-llm-evals.env
```

Systemd reads the root-only environment file before changing user. Keep
`QR_EVALS_BIND_HOST=127.0.0.1`, port `8830`, production-only CORS origin, and the
default data root; custom state paths also require sandbox/drop-in changes.
Keep `QR_EVALS_MAX_INFLIGHT=3` with the shared engine's four-sequence capacity
(one slot reserved for other users), unless its operator explicitly revises the
allocation. A replay profile with one sequence needs the harness cap reduced
to one. `QR_EVALS_TIER2_REPLAY_ACTIVE=1` is an operator declaration, not a switch
that configures the engine; readiness must still verify replay.

### Accounts, quotas, and the first admin

People who launch runs need an account. An account is a username, a password
and one linked api.entropic.science key; the account is created at
`https://evals.entropic.science/signup` and only if the key verifies. Accounts
and the token-usage ledger live in `/var/lib/qr-llm-evals/accounts.sqlite`,
which the service creates on first start.

Three settings in `/etc/qr-llm-evals/qr-llm-evals.env` control this:

| Setting | Default | Meaning |
|---|---|---|
| `QR_EVALS_SIGNUP_ENABLED` | `1` | `0` closes registration; existing accounts still sign in. |
| `QR_EVALS_DEFAULT_TOKEN_QUOTA` | `2000000` | Lifetime budget of qr-server generation tokens per account. `0` is unlimited. |
| `QR_EVALS_ALLOW_KEY_LAUNCH` | `0` | `1` lets a bare `qrk_` key launch without an account. Those runs are not metered and bypass quotas, so keep it `0` in production. |

The default quota is the operator's choice. The template ships `2000000`; a
deployment that should not limit anyone sets `0` and gives individual accounts
a limit later from the admin page.

Each person needs their own api.entropic.science key before they can sign up,
because one key links to one account. Keys are issued with qr-llm-api's tool,
which prints the key once and stores only its SHA-256. Hand it over a private
channel; a lost key cannot be recovered, only replaced:

```bash
KC="sudo -u qr-llm-api /opt/qr-llm-api/.venv/bin/python -m qr_llm_api.keys_cli"
$KC new --name <person> --rpm 600 --concurrency 6 --append /etc/qr-llm-api/api-keys.json
$KC list                       # names and limits, never digests
$KC disable --name <person>    # also stops that account from launching
```

The key's name is shown on the account page. `--rpm` and `--concurrency` are
the edge's own limits for direct API use; the harness token quota is separate.
The edge reloads the file within seconds and needs no restart.

Nobody is an admin until an operator says so. After the first person has
signed up, grant the flag on the box, as the service user so the database
keeps its owner:

```bash
cd /opt/qr-llm-evals/current
sudo -u qr-llm-evals env QR_EVALS_DATA_DIR=/var/lib/qr-llm-evals \
  .venv/bin/python -m qr_llm_evals.accounts.cli make-admin <username>
```

The same tool covers the rest. None of these commands prints or needs a
secret, except `reset-password`, which prompts for the new one:

```bash
... -m qr_llm_evals.accounts.cli list
... -m qr_llm_evals.accounts.cli set-quota <username> 5000000   # or 0, or default
... -m qr_llm_evals.accounts.cli disable <username>             # also signs them out
... -m qr_llm_evals.accounts.cli enable <username>
... -m qr_llm_evals.accounts.cli reset-password <username>
... -m qr_llm_evals.accounts.cli revoke-admin <username>
```

An admin can also set quotas and disable accounts from
`https://evals.entropic.science/admin`. There is no self-service password
reset: a user who forgets their password asks an operator.

How the quota behaves: only input and output tokens of calls on the
qr-server engine count. Calls on the user's own provider key, such as the
Anthropic judge, are recorded but not counted, because that key pays for
them. A judge or narrative model that is itself served by the engine does
count. An account with nothing left cannot launch or resume, and a run in
progress is paused when the budget reaches zero; raise the quota and the host
can resume it. The quota does not reset on a schedule.

What an account owes: the same engine tokens are priced, by default at $20
per million input and $80 per million output tokens for a quantum arm, and
$5 and $20 for everything else on the engine. The four prices are the
`QR_EVALS_PRICE_*` settings. The account page shows the result as
"Outstanding", and the admin page shows it per account. Nothing is charged
automatically and owing money does not stop a run. When a person has paid,
an admin presses "Mark paid" on the admin page, or runs:

```bash
... -m qr_llm_evals.accounts.cli balance <username>
... -m qr_llm_evals.accounts.cli settle <username> --note "invoice 12"
```

Marking an account paid records a payment for the amount owed at that moment
and keeps the usage history. Tokens used afterwards are owed again.

Storage: the account page lists how much disk each of the person's runs
uses, with a link to download the run's study bundle, and the admin page
shows the total per account next to the free space on the data volume.

Disabling a key in qr-llm-api's keys file also stops its account from
launching, because file mode re-checks the linked key's digest on every
launch. The account still signs in, so its owner can link a replacement key.
In `probe` mode that re-check is not possible: a key is trusted from the
moment it is linked, so disable the account as well as the key.

### Cross-repo keys-file permissions

File mode reads **digests only** from `/etc/qr-llm-api/api-keys.json`. Coordinate
with the qr-llm-api owner: preserve its owner and writer access, grant only read
access to this service group, and retain that group/mode after key rotations.
If the edge relies on the file's former group, preserve that access explicitly
before changing it. Directory traversal is also required:

```bash
sudo chgrp qr-llm-evals /etc/qr-llm-api
sudo chmod g+x /etc/qr-llm-api
sudo chgrp qr-llm-evals /etc/qr-llm-api/api-keys.json
sudo chmod 0640 /etc/qr-llm-api/api-keys.json
sudo -u qr-llm-evals test -r /etc/qr-llm-api/api-keys.json
```

qr-llm-api's key tool replaces the file atomically, which resets it to mode
0600 owned by `qr-llm-api`. Every new, updated or disabled key would then lock
the harness out, and signup and launch would refuse every key. Install the
watcher that puts the group and mode back whenever the file changes:

```bash
sudo install -o root -g root -m 0644 infra/qr-llm-evals-keys-perms.service \
  infra/qr-llm-evals-keys-perms.path /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now qr-llm-evals-keys-perms.path
```

Check it after the next key change with the `test -r` line above. These two
units are copied, not linked into the release, so re-install them when they
change in the repository.

Do not copy keys into this repository or make the file world-readable. If that
cross-repo permission change cannot be approved, use
`QR_EVALS_KEY_VERIFY=probe` and verify the edge at `127.0.0.1:8820`. A launch-key
probe may spend one generation token. Provider/judge keys are entered by hosts
in the UI and held only in memory; never put them in these environment files.

## 3. Install release units and deploy

1. Commit all intended implementation and generated changes; deploy rejects
   staged, unstaged, and non-ignored untracked files. Ignored caches never enter
   `git archive`. Resolve and review the selected commit, not just its branch.
2. Install this unit link once. If a regular unit already exists, inspect and
   back it up before replacing it; the script deliberately refuses mismatches.

```bash
sudo ln -s /opt/qr-llm-evals/current/infra/qr-llm-evals.service /etc/systemd/system/qr-llm-evals.service
sudo bash scripts/deploy.sh HEAD
sudo systemctl enable qr-llm-evals.service
sudo systemctl status qr-llm-evals.service --no-pager
curl --fail --max-time 10 http://127.0.0.1:8830/api/health
```

The script archives a committed ref into a new root-owned release, builds a
fresh venv using hashed runtime pins, runs `pip check`, and performs preflight
as `qr-llm-evals` with the real environment and filesystem restrictions **before
switching or restarting**. `release.env` fixes the commit and Python source
path; it contains no keys. Systemd repeats preflight at every start. A nonzero
preflight, including an engine warning, refuses activation. Following the
atomic switch, health must report `status=ok`, the **exact target commit**, and
`engine.reachable=true`; failure restores/restarts the previous release. On
first-install failure there is no previous service: the failed one is stopped
and its release retained. Read the journal; do not repeatedly restart blindly.

```bash
sudo journalctl -u qr-llm-evals.service -n 80 --no-pager
sudo systemd-analyze verify /etc/systemd/system/qr-llm-evals.service
```

Later releases repeat only the update and the deploy. Merge to `main`, bring
the commit to the box (step 3 of section 1), then:

```bash
cd /srv/qr-llm-evals
sudo git log --oneline -1          # the commit you reviewed
sudo bash scripts/deploy.sh HEAD
```

A change that touches only the web app, documentation or tests does not need
a backend deploy; the web app is deployed by Cloudflare from `main` (section 5).

No inbound public firewall rule is needed for 8830 or 8000. Confirm the API
socket using `ss -ltnp`; do not change the bind address to bypass the tunnel.

## 4. Public hostname on the shared tunnel

The harness has no tunnel of its own. `evals-api.entropic.science` is a second
public hostname on the tunnel that already serves `api.entropic.science`
(`qr-llm-api-cloudflared.service`, token mode, owned by qr-llm-api). Nothing is
installed on the box for this step and no token is handled.

1. In Cloudflare (Zero Trust, Networks, Tunnels), open the existing qr-llm-api
   tunnel. Do not create a new tunnel and do not refresh its token.
2. Add a public hostname: `evals-api.entropic.science`, service
   `http://127.0.0.1:8830`. Leave the existing `api.entropic.science` entry
   pointing at `http://127.0.0.1:8820` unchanged. The dashboard owns ingress
   and creates the proxied DNS record; never point the record at the box's
   public IP.
3. The running connector picks the new hostname up within seconds. Check from
   outside the box once local health passes:

```bash
systemctl is-active qr-llm-api-cloudflared.service
curl --fail --max-time 10 https://evals-api.entropic.science/api/health
curl --fail --max-time 10 https://api.entropic.science/health
```

What sharing the tunnel means:

- Both hostnames go down together if the tunnel is restarted, its token is
  rotated or the connector fails. Deploying or restarting the harness never
  touches it.
- The connector runs as the `qr-llm-api` user and reaches the harness only
  over loopback. The harness keeps its own user, port, data and rate limits.
- Removing the hostname in the dashboard takes the harness offline without
  affecting `api.entropic.science`.

Check external registry responses, browser CORS, and an authorized run's SSE
stream for at least 60 seconds (including idle keep-alives). Do not cache
`/api/*` or apply browser challenges that break API/SSE requests.

## 5. Cloudflare Workers (static web app)

The web app is deployed as a Cloudflare Worker that serves **static assets
only**. `.\wrangler.jsonc` at the repo root names the Worker, points at the
build output and switches on the single-page-application fallback. There is no
Worker script: no request runs code at Cloudflare.

In the dashboard, create the application from the GitHub repository (Workers
& Pages, Create, import a repository) and set:

- **Project name**: `qr-llm-evals`. It must equal `name` in `.\wrangler.jsonc`,
  or the build refuses to deploy.
- **Build command**: `pnpm install --frozen-lockfile && pnpm build`.
- **Deploy command**: `npx wrangler deploy` (the default).
- **Path** (root directory): `/`, the repo root. The pnpm workspace spans more
  than the web app, and `.\wrangler.jsonc` lives there.
- **Build variables**: `NODE_VERSION=24`; `PNPM_VERSION=10.34.5`, matching the
  package manager pin (update together if the repository pin changes); and
  `VITE_API_BASE=https://evals-api.entropic.science` (origin only, no `/api`
  suffix). These are build variables, read while `pnpm build` runs.
  `VITE_API_BASE` is public build-time configuration, never a key.
- **API token**: let the dashboard create one for the build. It is stored by
  Cloudflare, not in this repository.
- **Custom domain**: after the first successful deploy, open the Worker's
  Settings, Domains & Routes, and add the custom domain
  `evals.entropic.science`. This hostname is served by the Worker, not by the
  tunnel.

There is no output-directory field: the directory comes from
`assets.directory` in `.\wrangler.jsonc` (`apps/web/dist`). A path that is not
a file is answered with `index.html` and status 200
(`assets.not_found_handling`), which is what makes client-side routes survive
a refresh. See
[single-page applications on Workers](https://developers.cloudflare.com/workers/static-assets/routing/single-page-application/).
`.\apps\web\public\_headers` restricts connections to the API origin,
disallows framing/objects, and sets privacy headers; Vite copies it into the
output and Workers applies it to asset responses
([headers](https://developers.cloudflare.com/workers/static-assets/headers/)).
Scripts/fonts remain self-hosted; inline styles are permitted for
React-rendered layout, but inline scripts and `eval` are not. SVG figures may
load only from self or the API (plus data images).

Preview builds for other branches get their own `workers.dev` URL. Check a
build locally before pushing with `pnpm build` then
`npx wrangler deploy --dry-run`, or serve it with `npx wrangler dev`.

Validate a deep link such as `/docs/determinism` directly and after refresh;
check response CSP and that script assets have JavaScript MIME types, not HTML.
Test API fetches and figure downloads in the browser console. Preview hosts are
**not automatically in backend CORS**; explicitly approve a preview origin for
testing rather than wildcarding production CORS. No extra browser provider
origins belong in CSP; provider requests are server-side.

## 6. First readiness and pilot (PRD section 7.4)

A healthy HTTP server is not experimental readiness. From the UI, sign in,
configure Study 1 and launch; consult `.\docs\API.md` for the exact
`POST /api/readiness` request contract if operating by API.

### Configuring the study in the web app

The setup page is the first page of the web app. It opens on the Study 1
template with its recommended design, and keeps the study being edited as a
draft in the browser, so a reload or a later visit continues where it
stopped. The server stores nothing about a study until a run is launched.
The template is Study 1 as a designed study. Three things in it follow from
the design and are not in `studies/study1.yaml`: the `design` block itself,
a seed of their own (1001) for the biased arms, and 24 repetitions for
`self_referential_probe` where its card says 20, the fewest at which its 30
prompts are rated sufficient for its margin.

**The five steps.** The rail lists the steps and says in words how each
stands: "1 to fix", "1 to check" or "clear". Every step can be opened at any
time. Only a problem marked "Blocks launch" stops the pre-registration and
the launch; a warning does not.

1. **Compare.** The models and sampling presets to compare. Each ticked pair
   is its own comparison: one quantum arm against the mean of several seeded
   pseudo-random control runs. Pairs are never pooled. The step also sets the
   number of control seeds, the positive control (a deliberately biased arm,
   ε) and whether the study has supervised sessions. Unticking the last
   comparison and ticking one again keeps those choices: the positive
   controls, the observed arm and its session settings come back with it.
2. **Measure.** The evals that run in every arm, and for each one the number
   of prompts and the repetitions per prompt (k). The token-level measures
   always run. The step also holds the **token limit** of every eval that
   generates replies, which is where a reply is cut off. Each eval has two
   limits to pick from, or a number of your own between the usual length of
   its replies and 64,000:
   - **Standard** is the eval's own limit (`max_tokens` on its card) and
     what a study that sets nothing runs with.
   - **Safe** is four times the standard, at least 256 and at most 64,000.
     Pick it when a cut reply costs you more than a long one, as on the
     evals where the model thinks before it answers (`livecodebench`,
     `gpqa_diamond`, `aime2026`). In the pilot of 23 November every finished
     `livecodebench` reply on one hard problem reached the standard limit of
     10,000 tokens while the model was still thinking, so none was scored.

   The safe limit is a rule and not a measurement: nothing has checked that
   no reply reaches it, and a reply that does is flagged as cut like any
   other. A limit caps a reply and does not change the estimate, which rests
   on the usual length of a reply; the table's last column is the most one
   arm can generate on the eval if every reply runs to the limit. At about
   15 tokens per second per stream, one reply of 40,000 tokens takes about
   45 minutes, so look at that column before you take the safe limit on a
   large eval. A limit is part of the study file
   (`cards[].overrides.max_tokens`) and of its hash, so it is registered
   with everything else and is the same for every arm. The engine on the
   box serves 131,072 tokens of context, shared by the prompt and the reply;
   a request whose prompt and limit together exceed what a model serves is
   refused by that model, and a run of such refusals is stopped by the
   check for failed generations.
3. **Design.** What would count as a finding: the claim (a difference only,
   or a difference or its absence within a margin), the outcomes that decide
   the study, the false-finding rate (α), the positive control that must be
   detected, the smallest difference that matters for an outcome whose eval
   card states none, and what the researcher expects to see. Every choice
   starts at its recommended value. Below the choices the step shows what
   they add up to: one card per generated hypothesis with the verdicts it can
   end with, a sample-size table, and, folded, the rules the harness fixes.
   A study file without a `design` block shows the built-in Study 1 plan
   here with a "Declare a design" button; until it is pressed the study
   keeps that plan. Declaring a design adds the design block and changes
   nothing else: prompts, repetitions and arms stay as they are. Where an
   eval's repetitions are then too few for an outcome that decides the
   study, the mark beside them says so and "Use recommended" sets the k that
   would do. "Return to the built-in plan", folded on the same step, takes
   the design block out again. If nothing else was edited in between, the
   study is then the one it was before, with the same config hash.
4. **Instruments.** The judge and the way supervised sessions are run. The
   step is offered only when the study has a judged eval or supervised
   sessions.
5. **Review & launch.** Authors and contact, every problem of the study
   grouped by step with a link to its setting and its one-click fix, the
   server's hashes of the study as it stands (config, plan and
   pre-registration draft), the access keys, "Generate pre-registration",
   "Copy share link" and "Launch run". Keys typed here stay in the page for
   the visit: they are never written to the draft, to a study file or to a
   share link.

**Marks beside the settings.** A setting carries a mark that says how it
stands. For a sample size: enough for the stated margin (✓), close or
without a raw-unit margin (≈), too few prompts (↓), or no closed-form power
(?). For a choice: the recommended value (=), your own choice (≠), not
runnable (✕), or set by the harness. Each mark is a colour, a glyph and a
border style; a legend on the Design step and on the map explains them. The
sample-size marks are worst-case bounds computed before any data exist. They
are never green for the two token-level tests (KS and MMD), which have no
closed-form power. The repetitions (k) of an eval carry the "close" or the
"too few" mark, whichever its prompts are rated at that k, when a larger k
within the eval's bounds would make its prompts enough for an outcome that
decides the study. The mark names that k and "Use recommended" sets it. `.\docs\ANALYSIS_PLAN.md` ("Sample-size ratings and
their limits") says what the ratings do and do not mean.

**The study bar.** It stays under the site header on every step and over the
map. From left to right: the study's title with the state of its file under
it; whether the study is ready ("Ready", "Ready · 1 to check", or the number
of things to fix, which opens the settings on the problems); the
"Steps | Map" switch; "Load study file"; "Save study file"; a gear that opens
"All settings"; and "More" ("Save", "Save as…", "Start from Study 1
template", "Copy share link"). On a phone the bar has two rows; Save and the
gear stay in sight and "Load study file" moves into "More". "Ready" alone
means the study has neither a blocking problem nor a warning. The Study 1
template does not start there: it has one thing to fix (the placeholder
contact) and three to check (see "What Review & launch lists" below).

**Loading a study file.** Press "Load study file", or drop one file anywhere
on the setup page. YAML and JSON are read; a file may leave defaults out.

- A file is refused before it is read when it is larger than 200 kB or
  empty, or when more than one file is given.
- The server reads the file (`POST /api/config/parse`), and what it returns
  must also be a study the page can hold. A refusal is shown in the server's
  words with the setting it names.
- A file that was saved with problems loads. The page saves a draft as it
  stands, so a file may hold a study that is not consistent yet: an outcome
  whose eval was unticked, a claim of absence without a positive control, an
  arm on a seed the study does not list. The server reads such a file and
  lists what it would refuse at launch. The report then says "The file was
  saved with 1 problem that the server would refuse at launch. The study was
  loaded as it is, and what is wrong with it is listed under the steps.",
  with the server's own messages folded under it. The problems are listed
  in the page's words under their steps and block the launch until they are
  fixed. A file that is malformed is still refused: text that is not YAML or
  JSON, a missing or mistyped field, an unknown setting, a study without
  arms or evals.
- A whole number larger than 9007199254740991 (a seed, for example) is
  refused with the setting it is in: the server can read it and a browser
  cannot keep it exactly.
- If the server cannot be reached, a complete JSON study is loaded with the
  note "Loaded without the server's checks."; a YAML file is not loaded.
- A file that fails at any point changes nothing: the study on screen, its
  draft and its file state stay as they were.
- Before a loaded study, the template or the study of a share link replaces
  work that is in no file, the page asks "Save current study first",
  "Replace" or "Cancel". "Save
  current study first" replaces the study only after it is in a file that
  loads back: if the save dialog is closed, the file cannot be written, or
  the file that was written will not load back as it is, nothing is
  replaced, and the dialog stays open and says why.
- A report under the bar says what was loaded (arms, evals, hypotheses, or
  that the file has no design block), which evals this server does not have,
  and how many things are left to fix. The file state then reads
  `Saved to <name>`, because the study on screen is the one in that file.

**Editing in All settings.** The gear opens every setting of the study in one
long dialog, grouped as Study, Models & arms, Evals & repetitions, Design &
analysis, Supervised sessions, Judge & narrative and Advanced. It includes
the settings no step shows: the arm table, the model list, the seed list,
the estimate assumptions, the pilot, the session displays, the narrative
model and the recorded hashes. A filter finds a setting by its name, by the
label of a field or by its key in the study file (for example
`design.alpha`). "Problems only" keeps the settings that have a problem, and
each section lists its problems first with a link to the field. An edit
applies at once to the study behind the dialog; there is no separate apply
step. The footer has the file state, "Load study file", "Save study file"
and "Done".

**Saving a study file.** "Save study file" is in the study bar on every step
and on the map, and in the footer of All settings. It writes the study to a
`.study.yaml` file named after the study's title. Writing the file needs no
server and is never refused: it works offline, signed out and for a draft
with problems. After the file is written the page checks whether it will
load back (see "Whether a saved file will load back" below). How the file is
written depends on the browser, and the file state says only what the page
knows:

- **Where the browser has a save dialog** (Chromium browsers today), the
  first save asks where to put the file and the page writes it there. Later
  saves write the same file without asking. "Save as…" under "More" asks
  again. The file state reads `Saved to <name>` once the file was written
  and closed, under the name that was picked. Closing the dialog saves
  nothing and changes nothing. If the file cannot be written, the page says
  "The study could not be written to a file", with the browser's reason, and
  "Nothing was saved."
- **Elsewhere** (Firefox and Safari at the time of writing, or a page in
  which the browser refuses the dialog), the file is handed to the browser
  as a download. The browser decides where it goes and under which name, and
  it does not tell the page whether the file was written. The file state
  therefore reads `Downloaded as <name>`, never "Saved to". Check the
  downloads folder before relying on the file. "Save as…" does the same
  there, and the menu says so.

In both cases the state adds what is left to fix while anything blocks a
launch ("Saved to study.study.yaml · 2 things to fix"), reads
`Changed since <name>` after the next edit, and reads "Not saved to a file
yet" for a study
that was never saved or loaded. A study that takes the place of another (a
loaded file, the template, a share link) is never written into the earlier
study's file without asking.

**Whether a saved file will load back.** After every save the page sends the
text it wrote to the server (`POST /api/config/parse`, the route a load
uses; the text holds no key) and reports the answer under the bar:

- "The server read the same text back and accepts it, so the file will
  load." A study with problems that span several settings gets this answer
  too: such a file loads, and its problems are listed again when it does.
- "The server could not be asked to read it back. This page's own check
  found nothing in it that stops a study file from loading." This is the
  answer when the server is out of reach, answers with an error or is asked
  too often, or does not answer within 8 seconds.
- "This file will not load back as it is.", followed by the reason. The
  file state then reads `Saved to <name> · will not load back as it is`.
  This happens for a draft with a problem inside one setting: an empty title
  or contact, a study without arms or without evals, an arm whose own
  settings contradict each other, a value outside its allowed range. The
  file was still written. Fix the setting and save again.

The check runs after the file is written, because the browser's save dialog
has to open in the click that asked for it.

The file holds the whole study and none of the keys. A file that the server
accepts loads back as the same study: a file saved for Study 1 has the
config hash of Study 1
(`.\backend\tests\test_study_file_roundtrip.py`). Keep the saved file with
the study's records; the draft in the browser is a convenience, not an
archive. Two things to know: comments typed into a file by hand are not kept
when it is loaded and saved again, and a file that was loaded and saved
without an edit can differ in bytes from the file that was loaded (the
server writes out defaults the file left implicit), while it describes the
same study.

**Share links.** "Copy share link" (under "More" and on Review & launch)
copies a link that holds the study as it stands and none of the keys. It is
offered only for a study the page can hold. Opening such a link on the setup
page:

- takes the place of the stored draft at once when nothing is lost by that:
  the draft is the untouched template, or it equals its file;
- asks first when the draft has work that is in no file: "Save current study
  first", "Replace" or "Cancel". "Cancel" keeps the draft and its file, and
  the page says "The share link was not opened. The study you were editing
  is unchanged.";
- changes nothing when the link holds the draft itself.

In every case the link then leaves the address bar, so a reload opens the
draft with its later edits and not the link again. A study that came from a
link is "Not saved to a file yet". A link that cannot be read opens the
stored draft, or the template when there is none, and the page says which.
The pre-registration page shows the study of a link without changing the
stored draft, says so under "Study on this page", and has a link that opens
that study in the setup. When a link or a stored draft cannot be read there,
the page says which study it shows instead, and renders or shares it only
after "Continue with this study".

**The draft in the browser.** Every edit is stored in this browser at once.
Three cases to know:

- Two tabs on the setup follow each other: an edit, a save or a load in one
  shows in the other, so neither overwrites the other's work with an older
  study.
- If the browser does not store the draft (its storage is full, or blocked
  for the site), the page still works, and Review & launch lists the warning
  "This browser did not store the draft, so closing the page loses it. Save
  a study file to keep it."
- If a stored draft cannot be read by this version of the page, the Study 1
  template is loaded in its place and the page says so. The text that could
  not be read is kept in the browser and offered as "Download the draft that
  could not be read".

**What Review & launch lists.** The step lists every problem of the study
under the step it belongs to, with a link to its setting and its one-click
fix where there is one. A problem marked "Blocks launch" stops the
pre-registration and the launch. A warning does not; it says what weakens
the study or what the pre-registration will say about it. Besides the
problems of the design (too few prompts, a margin that cannot stand, a
verdict no outcome can reach), the page reports:

- **Pre-registration warnings, before they are frozen.** A pre-registration
  opens with validity warnings the study can only avoid before its first
  launch. The page lists each as a warning: a model without a pinned
  checkpoint ("Pin the server's checkpoint" fills it in where the server
  names one), supervised sessions whose questionnaires and target pool are
  not pinned by their hashes (the page cannot fill these in; they are
  written into the study file by hand), and a judge that is sent a
  temperature other than 0.
- **Too few prompts for the control.** An outcome whose eval has fewer
  prompts than the seed-against-seed control is asked for blocks the launch:
  "with 8 prompts it is not certain that the seed-against-seed control can
  fail, so the analysis does not count it as passed and the outcome could
  only come out invalid. At least 10 prompts are needed." Where an eval has
  no more prompts, the warning on its sample size names the repetitions (k)
  that would make them enough.
- **A token-level outcome with no data planned.** A study that decides on
  one of the two token-level tests and selects no eval with a prompt list of
  its own plans no data for that outcome. This blocks the launch; the way
  out is to select such an eval.
- **What a claim of absence cannot conclude.** A study that claims absence
  and decides on a token-level outcome is warned that the study as a whole
  cannot conclude "no effect within margins", and each outcome that can
  return "equivalent" is warned that this needs the biased arm to move it by
  a stated amount, which is not known before the run.
- **A study the page cannot keep.** A loaded file or a hand edit can leave a
  setting outside what a study can hold. Each such setting is named as a
  blocking problem. Until it is fixed the browser does not store the draft:
  the page says so beside the problem, and the file state in the bar ends
  with "· edits are not kept in this browser". A study file saved in that
  time will not load back.
- **An arm that contradicts itself.** The arm table in All settings lets
  every field of an arm be set on its own, so an arm can be given a seed
  that its source does not use, a bias ε on an arm that is not a biased arm,
  greedy decoding on a quantum arm, or be switched off without a reason. The
  page names each such arm and blocks the launch; the server refuses a file
  with such an arm.
- **References the server checks.** An id used twice, an arm on a model or
  a seed the study does not list, an observed arm without session settings,
  a comparison with two quantum arms or a repeated control seed, an outcome
  listed twice, a margin for an outcome that does not decide the study.
  The page names each and blocks the launch, so it is not first seen as the
  server's refusal.

**The launch dialog.** It offers a pilot and a full run whatever the study
says. "Preselect a pilot run in the launch dialog" (All settings, Advanced)
chooses which one is selected when the dialog opens. A study that was
already launched can be launched again with the same configuration: the new
run joins the registered study, whose pre-registration stays the one frozen
at its first launch. The page does not say whether a launch registers a
study or adds a run to a registered one. The document of record of a
registered study is at `/studies/<study_id>/prereg` in the web app and at
`/api/studies/<study_id>/prereg.md`. A document rendered later can have
another hash, because a pre-registration prints the readiness report it was
rendered with. A study with a design block can be launched
again only from the account that launched it first; anybody else is refused
and told to change the title or the creation time, which registers the
design as a study of their own.

**The map view.** "Map" in the study bar shows the same study as one
hand-drawn diagram, read from left to right: the question, the arms, the
prompts and their repetitions, what is measured, the tests, the gates and
the verdicts, with one lane per comparison.

- Drag to move, and zoom with the wheel, a pinch or the toolbar ("Zoom out",
  "Zoom in", "Fit", "Start"). Zoomed out, a node shows one sentence; at
  reading size, the numbers of the design; zoomed in, all its settings.
- A node that holds others unfolds in place: the seeded runs of the
  controls, the evals of a tier, the outcomes of an eval. "Fit" shows the
  whole study where it fits at a size that draws everything; where it does
  not, as it can with groups unfolded, "Fit" shows the zoomed-out picture.
- Each column heading states the problems of its column, as words at reading
  size and as counts when zoomed out, where some of the nodes they sit on
  are not drawn. Pressing it opens the settings on those problems.
- A node opens a popover with the fields of its settings, its problems with
  their one-click fix, and "Open in all settings". The fields are the ones
  of the steps and of All settings, so an edit on the map changes all three.
  Settings that are not a node (α, power and completeness, the narrative
  model, authors and contact, the sampling envelope, the session settings,
  the judge) are pencilled beside the place they belong to and open the same
  popover.
- An eval opens down to its single prompts, read from the run service a page
  at a time (`GET /api/cards/{id}/items`): the list, the picked prompt in
  full, the told cell where the eval has told cells, and which prompts lie
  beyond the study's chosen count. Ten evals can list their prompts before a
  run. For the others the map says why not: the prompts are built from the
  study's own generations, downloaded at run time, inside an Inspect task,
  or the measure is computed from the token log of the other evals and has
  no prompts of its own.
- Resting the pointer or the keyboard on a node keeps everything upstream
  and downstream of it inked and quiets the rest.
- "Outline" in the toolbar shows the same graph as a nested list with the
  same marks, fields and prompts. It is what a window of 760 px or less
  starts with.
- The nodes sit where the layout puts them; they cannot be dragged to new
  positions.

### From readiness to the full study

1. **Readiness**: verify fresh QRNG health/fallback baseline, arm availability,
   resolved-envelope equivalence, five solo PRNG replays and under-load behavior,
   achieved determinism tier, Tier-0 acquisition mode, and optional throughput.
   Save the readiness report and serving versions. Do not assert load invariance
   just because a seed is set. All arms pin `zscore_mean`, zero calibration,
   and prefetch off; they differ only in source/derived seed/optional epsilon.
2. **Pilot**: launch a run of the kind `pilot` with approximately
   **200 prompts** across QRNG supervised, QRNG unsupervised, and PRNG seeds
   **1, 7, 42**; include the epsilon arm only if supported. A pilot runs the
   study exactly as it is configured: the harness does not subsample it, and
   the `pilot.prompts` setting is recorded with the study and applied
   nowhere. To pilot on fewer prompts, lower the prompt counts on the Measure
   step and save that study as its own file; it then has a different config
   hash from the full study. Tier 0 consumes generation traces,
   so select generation tasks providing those prompts rather than expecting a
   zero-token diagnostic card to generate them. Keep pilot artifacts separate
   from the confirmatory full run. A 20-prompt smoke check is **not this pilot**.
   For a study with a design block, a pilot is labelled non-confirmatory and
   sets no margins, and only the first full run of the study is confirmatory:
   do not launch a full run to try something out. A full run that was
   launched and never started does not count while its state is `created`;
   one that started does, whatever became of it, and so does an unstarted
   one once the backend has been restarted (the restart moves it to
   `needs_keys`). The pilot and the full run of one study are
   launches of the same configuration from the same account: the second
   launch adds a run to the study the first one registered. A pilot on fewer
   prompts is a different configuration and so a different study.
3. **Inspect pilot outputs**: pairing/join keys, quantum provenance, every
   fallback taint, Tier-0 fields, replay results, scored tables and figures, and
   optional classifier/Inspect execution. Read "Output health" on the run
   page: it counts, for each arm and eval, the completed and failed samples,
   the taints, the flags of the result checks and what became of the
   judgements, and it lists the cautions. A flag kind that is frequent in a
   pilot (replies that end at the token limit, answers the scorer cannot
   extract, judge replies without a score) is the cheapest warning that a
   prompt, a token limit or a rubric needs fixing before the full run. Measure aggregate generation rate and
   observer pace. Defaults of **400 tok/s aggregate** and **45 tok/s reading**
   are assumptions, not measurements; revise estimates against the pilot (target
   within +/-50%) and freeze the revised config before the confirmatory run.
4. **Preregister**: after feasibility/pilot decisions, write expectations,
   freeze models/checkpoints, datasets, seeds, envelopes, endpoints, exclusions,
   and analysis plan. Export Markdown, record its SHA-256 and canonical config
   hash, and publish before the full run. Do not retroactively label pilot data
   preregistered. A subsequent config change requires a new preregistration.
5. **Full study**: run the frozen configuration, then script analysis, verified
   narrative, public results/bundle. Numbers and figures never come from the
   narrative model. Review privacy consent and the publication bundle first.

### When a check stops a run

The harness checks every reply, token record, score and judgement while a
run stores it (`.\docs\ANALYSIS_PLAN.md`, "Result checks"). Most of what it
finds is a flag, which changes nothing about the run. When a check finds the
run plainly broken, it stops the run: the run takes no new work, the
requests under way finish and are stored, and the run is then `paused`. A
stop deletes nothing, is never an abort, and the run does not go on by
itself.

**What you see.** The run page shows a notice headed "Stopped by a check"
above the run controls. It prints what the check saw with the real counts,
where and when, a tally of causes in the harness's own words (an HTTP
status, the name of an error class), up to three of the samples it counted,
what Resume recovers after this stop, who can resume, and that Abort is
final. The live page shows one line with a link to the run page. The same
record is in `run.json` under `halts` and at
`GET /api/runs/<run_id>/health`.

**What to do, for every stop.**

1. Wait until the run is `paused`. For a short time after the notice appears
   the run is still `running` while the requests under way finish, and
   Resume is refused until they have.
2. Find the cause and fix it outside the harness. The list below says where
   to look for each rule.
3. Press Resume on the run page, in the browser tab that holds the run's
   host token, or send `POST /api/runs/<run_id>/control` with
   `{"action": "resume"}` and the `X-Host-Token` header. The server issues
   the host token once, at launch, and never again. Resume is refused with
   `403` while the launching account's token quota is spent.
4. Resume starts a new pass over the run. It generates the samples that have
   no completed reply and asks the judgements that have no reply, with the
   keys the run holds at that time. It never generates a completed reply
   again, never scores a stored reply again and never asks the judge again
   for a reply that was received.
5. If the backend was restarted after the stop, the run is `needs_keys` and
   the stop is still open. Supply the keys on the run page, then press
   Resume. A run that was launched without any provider key is `paused`
   after a restart and needs only Resume.

Abort only when the run is not worth finishing. An aborted run cannot be
resumed, and the server analyses only a complete run. What is stored is
kept. For a study with a design block an aborted full run still counts as
the study's first full run, so a full run launched after it is not
confirmatory.

A provider key of a paused run can be replaced without a restart:
`POST /api/runs/<run_id>/keys` with the host token. The run page has a key
form for this on a stop by `judge_rejected`, and whenever the run is
`needs_keys`; for any other stop use the route.

**What each rule means and where to look.** The names in brackets are the
ones in `run.json` and in the health summary.

- **Generations are failing** (`generation_errors`). Eight generations in a
  row on one arm ended in an error before a reply was complete, or at least
  half of the last 40 generations of the run did. On an eval that runs
  through Inspect, one generation that does not complete stops the run. The
  tally names the HTTP statuses and error classes. For an arm on the shared
  engine, check `qr-sampler-vllm.service` and
  `curl http://127.0.0.1:8000/health`. For an arm on an external provider,
  a `401` or `403` means its key (replace it as described above), and a
  `429` or a `5xx` means the provider's limits or an outage. Resume tries
  every sample without a completed reply again. A sample that then completes
  is analysed like any other, although its failed attempt left a
  `stream_error` taint.
- **The quantum source is degraded** (`entropy_degraded`). On a quantum arm,
  ten samples in a row were tainted for an engine fallback or a degraded
  quantum source, or three samples in a row started while the engine's
  entropy check reported the source degraded or could not be reached. Check
  `curl http://127.0.0.1:8000/health/entropy` (`rpc_ok`,
  `currently_degraded`, `fallback_count`) and the quantum device behind it.
  The tainted samples are complete: they stay in the run, they are excluded
  from the analysis, and Resume does not generate them again, so the quantum
  arm has lost them. Resume only once the entropy check is healthy;
  otherwise the run stops again after three samples.
- **Token records are missing** (`tier0_failed`). Ten samples in a row on
  one arm have no usable token records. Check that `QR_TOKEN_LOG_DIR` of the
  engine and `QR_EVALS_TOKEN_LOG_DIR` of the harness name the same
  directory, that the harness's service user can read it, that the disk is
  not full, and that no clean-up rule removes log files before the harness
  has read them ("Cross-repo readiness dependencies" below). Those samples
  are complete and are not generated again. A sample tainted for its token
  records (`stateless_row`) is excluded from the analysis. A sample flagged
  for a missing token log keeps its reply, score and judgement and has no
  token records, so the token-level measures do not have it.
- **The scorer is failing** (`scorer_errors`). The scorer of one eval failed
  on ten samples in a row. The tally names the error class, and the service
  log has the message (`sudo journalctl -u qr-llm-evals.service`). The cause
  is in the eval's task module or in something it needs. A fix is a code
  change and a deployment, after which the run needs its keys again. The
  harness does not score a stored reply again, so the samples whose scorer
  failed stay without a score.
- **Replies are empty** (`empty_outputs`). Twenty replies in a row on one
  eval were empty, they came from at least three prompts, and at least two
  arms each gave at least 5 of them. A reply that has reasoning text and
  ended at the token limit is not counted. Read the
  three samples the notice names in the eval's `generations.jsonl`, and
  check the model that is served and its chat template. Empty replies are
  complete samples: they stay in the run and in the analysis. Resume goes on
  with the samples that are left, and this check is not applied to that eval
  again in the run. If the cause is in the study itself (a prompt, a token
  limit), abort, correct the study and launch it as a new study.
- **No answer can be extracted** (`nothing_parsed`). On an eval without an
  LLM judge, none of the eval's scored samples so far gave an answer the
  eval's scorer could extract, and there are at least 30 of them from at
  least two prompts. The stop can come later than the 30th sample: the count
  goes on until the samples cover a second prompt. Compare a few replies with the format the eval's
  prompt asks for. Look at the flags of the eval under "Output health":
  replies that end at the token limit or that hold reasoning text point to
  the token limit or the thinking setting. The samples stay as they are and
  are not scored again. Resume goes on, and this check is not applied to
  that eval again in the run.
- **Samples are missing** (`unfinished`). Generation reached the end of its
  queue and some arm and eval has no completed sample, or more than 2% of
  its planned samples (and at least 3) have none. The same is checked at the
  end of judging for judgements without a reply. The tally gives the number
  missing for each arm and eval. This usually follows errors that were too
  few or too scattered to stop the run earlier. Resume tries the missing
  samples and judgements again. The check stops a run once for generation
  and once for judging: if samples are still missing when that part ends
  again, the run goes on and completes without them. In a designed study a
  hypothesis that analysed less than 90% of its planned data is then
  `invalid`.
- **The judge refuses requests** (`judge_rejected`). The judge answered with
  HTTP status 401, 403 or 404. One small request with the study's judge
  settings is also made before the first generation, on every start and
  every resume, when the run holds a judge key and one of its evals is
  scored by the study's judge; there a 400 counts as well, and a stop at
  that point means nothing was generated yet. For 401 or 403, supply a key
  that works in the form the notice shows (the study's judge uses the slot
  `anthropic`), then press Resume. A 404 or a 400 means the provider refuses
  the judge model or a judge setting. Those are frozen in the study, so
  Resume cannot fix them: abort, correct the judge in the setup ("Test the
  judge" on the Review & launch step makes the same kind of request) and
  launch the corrected study, which is a new study.
- **The judge is not answering** (`judge_unusable`). Ten judge calls in a
  row got no reply, or at least half of the last 40 did. The tally names the
  statuses: a `429` is the provider's rate limit, a `5xx` or a transport
  error an outage or the network. Judgements that got a reply are kept.
  Resume, when the provider answers again, asks only the judgements that
  have no reply.
- **No judge reply can be read** (`nothing_judged`). No score could be read
  from any of the first 30 judge replies for one eval. The reader takes a
  last line of the form `SCORE: n` or a JSON object with a numeric `score`,
  and nothing else, so the rubric and the judge's replies do not agree on
  the format, or the judge's token limit cuts the reply before the score
  (the flag "judge reply cut short"). Those replies are final: they are kept
  without a score and the judge is not asked again for them. Resume goes on
  with the judgements that are left, and this check is not applied to that
  eval again. The rubric and the judge settings are frozen in the study, so
  the fix is a corrected study.
- **The judge phase failed** (`judge_phase_error`). The judge phase ended on
  an error the harness did not expect. The tally names the error class, and
  the service log has the message. Every row already written is kept. Resume
  starts the judge phase again. If the same error comes back the run stops
  again, every time.

A run made before result checks shows no flag counts under "Output health"
and says so. If it is resumed under the current harness, the stop rules
apply to it.

### Cross-repo readiness dependencies

- **DEP-1**: the per-request token log is written by qr-sampler
  (`qr_sampler.logging.token_log`). To switch it on:
  1. in `/etc/qr-server/qr-server.env` set
     `QR_TOKEN_LOG_DIR=/var/lib/qr-sampler/token-log` and restart
     `qr-sampler-vllm` (its unit's `StateDirectory` creates `/var/lib/qr-sampler`);
  2. in `/etc/qr-llm-evals/qr-llm-evals.env` set `QR_EVALS_TOKEN_LOG_DIR` to the
     same path and restart `qr-llm-evals`.

  The harness only reads the directory; do not give it write access. With the
  directory set, every generation on the engine asks for its log
  (`qr_token_log`, `qr_request_id`), and readiness reports `tier0_source:
  engine_log` once a probe's file appears. Without the records the acquisition
  path is `api_logprobs`: label it degraded; PIT residuals (T0-a) and
  stream-deviation T4-l are unavailable, not approximated.

  A log file is a few hundred bytes per generated token (the candidate list is
  kept for the first token of a reply only) and is copied into the run's
  `tier0.jsonl` when the reply finishes, so the engine-side files are
  duplicates afterwards. Remove old ones with a `systemd-tmpfiles` rule on the
  box, for example in `/etc/tmpfiles.d/qr-sampler-token-log.conf`:

  ```
  e /var/lib/qr-sampler/token-log - - - 30d
  ```

  That rule only touches the engine-side directory, never run artifacts.

  A log usually has a few more rows than the reply has tokens: the engine
  samples for a few more steps after the token that ends the reply and
  discards the result. The harness keeps the rows for the reply's tokens.
- **DEP-2**: epsilon-biased seeded PRNG also belongs in qr-sampler. Unsupported
  arms remain unavailable; record the missing positive control in the prereg.
- **DEP-3**: entropy conditioning is served by Qbert0G and requested through
  qr-sampler. A study with `conditioning.mode: hash_truncate` needs
  `conditioning.allow_request: true` in `/etc/qbert0g/qbert0g.config.yaml` and
  a qr-sampler that knows the `qr_conditioning_*` keys; qr-sampler's
  `deployments/qr-server/README.md` describes both. Readiness sends every
  arm's real envelope, so an engine that lacks either refuses the arm there
  and not in the run. A hashed token reads eight times the raw bytes of
  a raw one at the default parameters; the request must stay within the
  entropy key's `max_bytes_per_request`. The harness sends the draw size
  itself: every envelope carries `qr_sample_count: 10000`, so a token draws
  10,000 bytes whatever the engine's process defaults are, and the engine's
  environment no longer carries `QR_PRESET`.
- **Stop/go**: absent primary measurements or required dependencies require a
  documented protocol decision before preregistration. Never silently count
  missing tests as evidence for a null effect. Do not claim consciousness
  influence, quantum purity percentages, or metaphysical conclusions from
  source labels, model self-report, or a feasibility pilot.

## 7. Supervised sessions over days

Schedule observer availability and breaks from measured throughput and chosen
display mode, not just GPU throughput. The initial budget assumed roughly 30
observer-hours single-stream, or 8 hours with four tiled streams; these are
planning estimates and four-stream capacity is not guaranteed by a three-slot
harness cap. Share a calendar schedule outside the harness; it never launches
sessions automatically.

1. Obtain consent and pre-questionnaires from pseudonymous observers. Protect
   the host token separately from the public observer join code.
2. Briefing then intention time (default 3 minutes), followed by an explicit
   host **Start**. Timers may go negative without initiating generation.
3. Plan 30-minute sessions with 10-minute breaks and 30-second refocus, adapting
   the schedule to people. Stop dispatch near the boundary and let in-flight
   generations finish; a modest overrun is expected. Only host **Resume**
   returns the session to running. Collect post-questionnaires per session.
4. At day end, pause at a completed-generation boundary. Keep the same frozen
   study and run across days; verify persisted progress before continuing.
   Browser disconnect does not stop the server-side run. Following a backend
   restart, re-enter BYO keys when `needs_keys` is shown and explicitly resume
   (a run launched without keys is shown as `paused` and is resumed directly);
   inspect incomplete/tainted records instead of rerunning paid samples blindly.
   A result check that stops the run ends the sitting at its boundary, as a
   host pause does; see "When a check stops a run" in section 6.

## 8. Maintenance, backups, and rollback

Operator tooling that launches with a bare key, such as `scripts/smoke.py
--live` with `--launch-key-file`, is refused while
`QR_EVALS_ALLOW_KEY_LAUNCH=0`. Set it to `1` for the acceptance window,
restart, run the check, then set it back and restart again.

### Daily backup of accounts and configuration

`accounts.sqlite` cannot be rebuilt: unlike `index.sqlite` it is not derived
from the artifacts, and losing it loses every account, quota and usage record.
Install the daily backup once:

```bash
sudo install -o root -g root -m 0755 infra/qr-llm-evals-backup.sh /usr/local/sbin/qr-llm-evals-backup
sudo install -o root -g root -m 0644 infra/qr-llm-evals-backup.service \
  infra/qr-llm-evals-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now qr-llm-evals-backup.timer
sudo systemctl start qr-llm-evals-backup.service     # take the first one now
sudo ls -l /var/backups/qr-llm-evals
```

Every night at 03:30 it writes two files to `/var/backups/qr-llm-evals`
(root only) and keeps the newest 60 of each:

- `accounts-<UTC>.sqlite`: a copy taken with SQLite's online backup API, so
  the service keeps running, and checked with `PRAGMA integrity_check`.
- `etc-<UTC>.tgz`: `/etc/qr-llm-evals` and `/etc/qr-llm-api` (environment
  files, the tunnel token, key digests).

`accounts-latest.sqlite` and `etc-latest.tgz` point at the newest pair. Like
the watcher, these files are copied, not linked into the release.

A backup on the same disk does not survive losing the box. Pull the newest
pair to another machine regularly and compare checksums:

```bash
f=$(ssh qr 'sudo readlink /var/backups/qr-llm-evals/accounts-latest.sqlite')
ssh qr "sudo cat /var/backups/qr-llm-evals/$f" > "$f"
ssh qr "sudo sha256sum /var/backups/qr-llm-evals/$f"; sha256sum "$f"
```

The off-box copy holds password hashes, key digests and, in the `etc`
archive, the tunnel token: keep it private and out of any repository.

To restore accounts, stop the service, put the backup in place with the
service user as owner, and start again. The current database and its `-wal`
and `-shm` files are moved aside first, never deleted:

```bash
sudo systemctl stop qr-llm-evals.service
sudo install -d -o root -g root -m 0700 /var/backups/qr-llm-evals/replaced-<UTC>
sudo mv /var/lib/qr-llm-evals/accounts.sqlite* /var/backups/qr-llm-evals/replaced-<UTC>/
sudo install -o qr-llm-evals -g qr-llm-evals -m 0600 \
  /var/backups/qr-llm-evals/accounts-<UTC>.sqlite /var/lib/qr-llm-evals/accounts.sqlite
sudo systemctl start qr-llm-evals.service
```

### Backup of run artifacts

The same nightly job copies the run artifacts (samples, token records, judge
rows; everything under `studies/` and `_quarantine/`) to
`/var/backups/qr-llm-evals/runs/`. Files only ever arrive in that copy:
nothing is deleted from it, so a run removed or quarantined in the live data
by mistake is still there. When a file changed since the last night, the
copy's previous version is first moved to `runs/changed-<UTC>/`, so a file
that was damaged or cut short in the live data cannot silently replace a good
copy. A finished run does not change and adds nothing there; a run that is
still generating adds its growing files once per night. The job never removes
these folders. Once a study is finished and pulled off the box, an operator
may remove old `changed-*` folders by hand.

`index.sqlite` is not copied: it is rebuilt from the artifacts.

This copy sits on the same disk as the data. It guards against a mistake, not
against losing the disk. Pull it to another machine, together with the
accounts database, with the script in the repository:

```bash
mkdir -p ~/Backups/qr-llm-evals && cd ~/Backups/qr-llm-evals
bash /path/to/qr-llm-evals/scripts/pull-backup.sh --runs
```

It downloads the newest accounts database and configuration archive and checks
their SHA-256 against the box, then downloads one `runs-<UTC>.tar.gz` with
every run and checks that the archive reads back and holds at least as many
files as the box reported. Without `--runs` it pulls only the first two. It
changes nothing on the box. Do this after the pilot, after the full run, and
before any maintenance on the box.

Disk space: the admin page shows the free space on the data volume and the
space each account's runs use. A full thesis run is a few gigabytes; the copy
doubles that.

### Maintenance

Pause active runs at generation boundaries before maintenance. For the run
artifacts, stop the harness while copying the data tree, or coordinate an
artifact snapshot with a paused run. Encrypt backups, restrict access, test
restores offline, and record commit/schema versions. Never delete paid-for artifacts; use quarantine
or withdrawal labels. Neither deployment nor rollback purges data or old releases.

```bash
# From the reviewed checkout on the box, after checking backup/schema compatibility:
sudo bash scripts/rollback.sh
curl --fail --max-time 10 http://127.0.0.1:8830/api/health
```

Rollback preflights `previous`, flips it into `current`, restarts, and verifies
its exact commit. Success makes the old current the new previous, so a second
rollback toggles back. Failure attempts to restore the original current and
returns nonzero; inspect the journal even if restoration succeeds. Configuration,
data schema, and Cloudflare dashboard settings are **not** rolled back. Apply
reviewed environment changes separately and only use compatible code releases.

Do not roll back to a release from before the result checks while runs made
with the checks must stay readable. Such a run has `checks_version` (and,
after a stop, `halts`) in its `run.json` and `status` on its judge rows. A
release from before the checks does not know these fields and refuses to
load the run. The files are not changed by a rollback, so the run is
readable again once a release with the checks is current.
Web app rollback is separate: roll the Worker back to a previously validated
version in its Deployments tab,
then recheck browser/API contract compatibility. No deployment script publishes
DNS changes or spends study tokens beyond preflight/health probes.
