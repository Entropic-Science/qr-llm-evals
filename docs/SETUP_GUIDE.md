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

How the quota behaves: only prompt and completion tokens of generations on
the qr-server engine count. Judge and narrative calls are recorded but not
counted, because the user's own provider key pays for them. An account with
nothing left cannot launch or resume, and a run in progress is paused when
the budget reaches zero; raise the quota and the host can resume it. The
quota does not reset on a schedule.

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

1. **Readiness**: verify fresh QRNG health/fallback baseline, arm availability,
   resolved-envelope equivalence, five solo PRNG replays and under-load behavior,
   achieved determinism tier, Tier-0 acquisition mode, and optional throughput.
   Save the readiness report and serving versions. Do not assert load invariance
   just because a seed is set. All arms pin `zscore_mean`, zero calibration,
   and prefetch off; they differ only in source/derived seed/optional epsilon.
2. **Pilot**: select a separate `pilot` run with approximately **200 prompts**
   across QRNG supervised, QRNG unsupervised, and PRNG seeds **1, 7, 42**;
   include the epsilon arm only if supported. Tier 0 consumes generation traces,
   so select generation tasks providing those prompts rather than expecting a
   zero-token diagnostic card to generate them. Keep pilot artifacts separate
   from the confirmatory full run. A 20-prompt smoke check is **not this pilot**.
3. **Inspect pilot outputs**: pairing/join keys, quantum provenance, every
   fallback taint, Tier-0 fields, replay results, scored tables and figures, and
   optional classifier/Inspect execution. Measure aggregate generation rate and
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

### Cross-repo readiness dependencies

- **DEP-1**: per-request token-log export belongs in qr-sampler. Only set
  `QR_EVALS_TOKEN_LOG_DIR` after the export exists and the service can traverse
  and read it. Grant group-read, not harness write access. Without the required
  records the acquisition path is `api_logprobs`: label it degraded; PIT
  residuals (T0-a) and stream-deviation T4-l are unavailable, not approximated.
- **DEP-2**: epsilon-biased seeded PRNG also belongs in qr-sampler. Unsupported
  arms remain unavailable; record the missing positive control in the prereg.
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
   restart, re-enter BYO keys when `needs_keys` is shown and explicitly resume;
   inspect incomplete/tainted records instead of rerunning paid samples blindly.

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

This backup does **not** cover run artifacts under `/var/lib/qr-llm-evals`
(samples, judge rows, bundles). Those are paid-for data and need their own
snapshot before a study produces any.

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
Web app rollback is separate: roll the Worker back to a previously validated
version in its Deployments tab,
then recheck browser/API contract compatibility. No deployment script publishes
DNS changes or spends study tokens beyond preflight/health probes.
