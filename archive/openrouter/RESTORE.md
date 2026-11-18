# OpenRouter provider profile (archived)

OpenRouter was one of the external provider profiles a study could pick as a
`provider` arm. It was removed from the live code and the app in November 2026.
Nothing here is imported, loaded or tested. This file keeps every removed piece
so the profile can be put back in one change.

## Restoring it

1. Add the profile row to `config/providers.yaml`:

   ```yaml
   openrouter:
     base_url: https://openrouter.ai/api/v1
     accepted_params: [temperature, top_p, min_p, seed, max_tokens, stop]
     seed_honoured: false        # forwarded to the upstream vendor; not guaranteed
     logprobs_honoured: false    # vendor-dependent; assume absent
     top_logprobs_cap: 0
     notes: min_p is passed through to vendors that support it; others ignore it.
   ```

2. Add the transport facts to `backend/src/qr_llm_evals/client/profiles.py`,
   and export `OPENROUTER_HEADERS` in `__all__`:

   ```python
   #: Headers OpenRouter asks apps to send (attribution; harmless elsewhere, sent only there).
   OPENROUTER_HEADERS: Mapping[str, str] = {
       "HTTP-Referer": "https://evals.entropic.science",
       "X-Title": "qr-llm-evals",
   }

   _TRANSPORT_FACTS = {
       "openrouter": ("openrouter", OPENROUTER_HEADERS),
       ...
   }
   ```

3. Add `"openrouter"` back to these four places:

   | File | Symbol |
   |---|---|
   | `backend/src/qr_llm_evals/models/schema.py` | `Provider` literal |
   | `backend/src/qr_llm_evals/models/catalogue.py` | `DYNAMIC_PREFIXES` (`"openrouter": "openrouter"`), which enables `openrouter:<vendor>/<model>` ids |
   | `backend/src/qr_llm_evals/api/routes/models.py` | `KEY_SLOT_BY_PROVIDER` (`"openrouter": "openrouter"`) |
   | `backend/src/qr_llm_evals/keys/secrets.py` | `KNOWN_PROVIDERS` (first entry) |

4. In `apps/web/src/routes/configure/index.tsx`, add `"openrouter"` to the
   provider allow-list in `modelRef` and to the slots the external key is sent
   to in `providerKeys`.

5. Regenerate the contracts (`python scripts/export_schemas.py`) and run the
   gate (`python scripts/check.py`).

## Tests that covered it

```python
# backend/tests/test_client.py
def test_extra_headers_and_key_are_sent() -> None:
    rec = _Recorder()
    client = rec.client(api_key="sk-or-abcdefghijklmnop", extra_headers=OPENROUTER_HEADERS)
    client.generate(BODY)
    headers = rec.requests[0].headers
    assert headers["authorization"] == "Bearer sk-or-abcdefghijklmnop"
    assert headers["x-title"] == "qr-llm-evals"


def test_known_targets_come_from_providers_yaml() -> None:
    targets = known_targets()
    assert targets["openrouter"].base_url == "https://openrouter.ai/api"
    assert targets["openrouter"].secret_name == "openrouter"
    assert targets["openrouter"].extra_headers == dict(OPENROUTER_HEADERS)


def test_target_for_resolution() -> None:
    by_url = target_for("custom", "https://openrouter.ai/api/v1")
    assert by_url.id == "openrouter" and by_url.secret_name == "openrouter"


# backend/tests/test_envelope.py, inside test_provider_body_uses_profile
orr = build_request_body(arm, ext, CTX, MESSAGES, profile=profiles["openrouter"])
assert orr["min_p"] == 0.10 and "logprobs" not in orr


# backend/tests/test_provenance.py, inside test_catalogue_pins_only_the_loopback_target
assert cat.is_pinned("openrouter:anthropic/claude-sonnet-4") is False
```

## What stayed in the live code

Three safety nets still recognise the `sk-or-` key shape, so a key pasted into
the wrong field is still caught: the log redaction pattern in
`logging_utils.py`, the committed-secret scan in `backend/tests/test_hygiene.py`
and the `OPENROUTER_API_KEY=` guard in `backend/tests/test_infra.py`.
