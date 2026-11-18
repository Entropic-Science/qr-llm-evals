# Privacy, publication, and withdrawal

The harness is designed for **public, reproducible research**, not private chat.
Study hosts must tell participants what will be collected before a supervised
session begins and obtain the consent recorded by the session flow.

## What becomes public

A published study bundle can contain:

- the frozen study configuration, pre-registration, expectations, analysis plan,
  code/tool versions, and integrity hashes;
- prompts, model outputs (including reasoning text when the study enabled it),
  token-level sampling provenance, scores, timing, and taint records;
- participant-chosen display names, random observer identifiers, intentions,
  questionnaire answers, and free text submitted to the study; and
- script-generated JSON tables and SVG figures, plus the checked LLM-written
  narrative.

Do not submit real names, contact details, secrets, or sensitive personal
information as a display name, intention, questionnaire response, prompt, or
free-text answer. A random observer identifier is not an anonymous identifier
if it is reused alongside identifying text.

## What an account stores

Launching runs needs an account. The server keeps, in a database that is never
part of a published bundle:

- the username you chose;
- your password as a salted scrypt hash, never the password;
- the SHA-256 of your api.entropic.science key, its name in the keys file and
  an 8-character fingerprint, never the key;
- the SHA-256 of each sign-in session token and when it expires; and
- one row per model call made by your runs: which run, which arm and eval,
  the model, the randomness source label, the token counts and the time. No
  prompt text and no model output is stored there.

Operators and accounts flagged as admin can see every account's username,
key name, token totals and quota. Choose a username you are content for them
to see; it does not need to be your name. An account can be disabled but is
not deleted, because runs keep a reference to the account that launched them.
To have an account's records removed, contact the operator.

Your browser keeps the session token and the public account fields in
localStorage until you sign out or the session expires. It never stores your
password or your key.

## What is never published

- Launch bearer keys, provider API keys, and host tokens are never persisted.
  Only boolean `keys_present` facts are stored while a run exists.
- Public study summaries omit the persisted host-token hash, launch-key name,
  launching account id and key-presence map. Published bundles omit the
  launching account id as well.
- Bundle export recursively strips provider request identifiers, response or
  request header dumps, `x-*` metadata, and related provider-routing metadata.
- The rebuildable SQLite index and quarantined artifacts are not in a bundle.

Provider services still receive the prompts sent to their models. Their own
retention and privacy terms apply; a host should choose providers accordingly.

## Licences

- **Code:** Apache License 2.0 (`LICENSE`).
- **Documentation, pre-registrations, and results prose:** CC BY-SA 4.0
  (`LICENSE-docs`).
- **Raw study data:** CC BY 4.0 (`LICENSE-data`, also copied into every frozen
  study bundle).

These licences permit downstream copying. Withdrawal from this server cannot
recall bundles that other people have already downloaded or redistributed.

## Withdrawal and retention

A host can withdraw a run with `POST /api/studies/{study_id}/withdraw` and the
run's host token. Withdrawal:

1. marks the durable run record as `withdrawn`;
2. writes a visible `WITHDRAWN.md` banner with the reason and time; and
3. keeps all paid-for QRNG, model, and analysis artifacts in place for the
   scientific record.

Withdrawal **never deletes data**. Withdrawn runs are labelled in the public
study response and bundle and are excluded from subsequent pre-registered
analysis. Invalid artifacts are moved to the dated `_quarantine` area rather
than deleted; quarantine is not included in public bundles.

For a request concerning already-published personal data, contact the study
contact recorded in `study.json`. The project can label or stop serving its own
copy, but cannot control copies already obtained under the stated licence.
