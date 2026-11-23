"""``GET /cards/{id}/items`` and ``runner.preview``: prompts a researcher can read before a run.

The route is open and must be side-effect free: it lists a card's prompts only
when the task module builds them from files in this repository, says why not
otherwise, and answers ``200`` even when a task module is broken. The tests run
against the real registry, because the claim under test is about the real
cards: which of them can be listed, and that listing them touches no network.
"""

from __future__ import annotations

import json
import socket
import urllib.request
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from qr_llm_evals.api import create_app
from qr_llm_evals.api.ratelimit import TokenBucketLimiter
from qr_llm_evals.api.routes.cards import ITEMS_DEFAULT_LIMIT, ITEMS_MAX_LIMIT
from qr_llm_evals.cards import EvalCard, load_cards
from qr_llm_evals.config import Settings, load_settings
from qr_llm_evals.keys.verify import FileKeyVerifier
from qr_llm_evals.runner import preview
from qr_llm_evals.runner.preview import (
    CONTENT_LIMIT,
    PREVIEW_SAFE,
    build_preview,
    classify,
)
from qr_llm_evals.runner.tasks import load_task_module, told_preamble
from qr_llm_evals.store import Store

API = "/api"
STUB_REGISTRY = Path(__file__).resolve().parent / "fixtures" / "registry"
#: A listable card of the real registry that also has the told factor.
TOLD_CARD = "self_referential_probe"
#: A listable card without it, large enough to page.
PLAIN_CARD = "sentience_statements"


@pytest.fixture(scope="module")
def registry() -> dict[str, EvalCard]:
    return load_cards()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    keys = tmp_path / "api-keys.json"
    keys.write_text("[]", encoding="utf-8")
    return load_settings(
        {
            "QR_EVALS_DATA_DIR": str(tmp_path / "data"),
            "QR_EVALS_KEYS_FILE": str(keys),
            "QR_EVALS_VLLM_ORIGIN": "http://stub",
        }
    )


def _app(settings: Settings, **overrides: Any) -> FastAPI:
    return create_app(
        settings,
        store=Store(settings.data_dir),
        verifier=FileKeyVerifier(settings.keys_file),
        limiter=TokenBucketLimiter(rate_per_s=1000.0, burst=1000),
        **overrides,
    )


async def _serve(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async for c in _serve(_app(settings)):
        yield c


async def _items(client: httpx.AsyncClient, card_id: str, **params: Any) -> dict[str, Any]:
    resp = await client.get(f"{API}/cards/{card_id}/items", params=params)
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _assert_error(resp: httpx.Response, status: int, param: str) -> None:
    assert resp.status_code == status, resp.text
    error = resp.json()["error"]
    assert error["code"] == status and error["param"] == param


# ── paging ────────────────────────────────────────────────────────────────────


async def test_pages_follow_the_task_module_order(
    client: httpx.AsyncClient, registry: dict[str, EvalCard]
) -> None:
    task = load_task_module(registry[PLAIN_CARD])
    expected = task.build_items()

    first = await _items(client, PLAIN_CARD)
    assert first["listable"] is True and first["source"] == "task_module"
    assert first["reason"] is None and first["told"] is None
    assert first["total"] == len(expected) == registry[PLAIN_CARD].dataset.items
    assert (first["offset"], first["limit"]) == (0, ITEMS_DEFAULT_LIMIT)
    assert [item["index"] for item in first["items"]] == list(range(ITEMS_DEFAULT_LIMIT))

    second = await _items(client, PLAIN_CARD, offset=ITEMS_DEFAULT_LIMIT, limit=7)
    assert [item["index"] for item in second["items"]] == list(range(50, 57))
    listed = first["items"] + second["items"]
    assert [item["prompt_id"] for item in listed] == [e["prompt_id"] for e in expected[:57]]
    # The messages are the ones the runner would send, turn for turn.
    for item in listed:
        assert item["messages"] == task.build_messages(expected[item["index"]])
        assert item["truncated"] is False

    last = await _items(client, PLAIN_CARD, offset=len(expected) - 3, limit=10)
    assert [item["index"] for item in last["items"]] == list(range(897, 900))
    past = await _items(client, PLAIN_CARD, offset=len(expected), limit=10)
    assert past["items"] == [] and past["total"] == len(expected)


async def test_limit_is_capped(client: httpx.AsyncClient) -> None:
    full = await _items(client, PLAIN_CARD, limit=ITEMS_MAX_LIMIT)
    assert len(full["items"]) == ITEMS_MAX_LIMIT == 100
    url = f"{API}/cards/{PLAIN_CARD}/items"
    _assert_error(await client.get(url, params={"limit": ITEMS_MAX_LIMIT + 1}), 400, "query.limit")
    _assert_error(await client.get(url, params={"limit": 0}), 400, "query.limit")
    _assert_error(await client.get(url, params={"offset": -1}), 400, "query.offset")


async def test_unknown_card_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"{API}/cards/nope/items")
    _assert_error(resp, 404, "card_id")
    assert resp.json()["error"]["type"] == "not_found_error"


# ── told cells and truncation ─────────────────────────────────────────────────


async def test_told_cell_selects_the_preamble(client: httpx.AsyncClient) -> None:
    default = await _items(client, TOLD_CARD, limit=3)
    assert default["told"] == "told_nothing"
    by_cell = {
        cell: await _items(client, TOLD_CARD, limit=3, told=cell)
        for cell in ("told_qrng", "told_prng", "told_nothing")
    }
    assert by_cell["told_nothing"] == default
    for cell, page in by_cell.items():
        assert page["told"] == cell and page["total"] == 30
        assert [i["prompt_id"] for i in page["items"]] == [i["prompt_id"] for i in default["items"]]
    first = {cell: page["items"][0]["messages"][-1]["content"] for cell, page in by_cell.items()}
    assert len(set(first.values())) == 3
    # Each cell differs from the next only by what the model is told about its sampler.
    assert (
        "quantum" in first["told_qrng"].lower() and "quantum" not in first["told_nothing"].lower()
    )
    assert told_preamble("told_qrng") != told_preamble("told_prng")

    _assert_error(
        await client.get(f"{API}/cards/{TOLD_CARD}/items", params={"told": "told_everything"}),
        400,
        "query.told",
    )


async def test_told_is_ignored_on_a_card_without_the_factor(client: httpx.AsyncClient) -> None:
    plain = await _items(client, PLAIN_CARD, limit=5)
    asked = await _items(client, PLAIN_CARD, limit=5, told="told_qrng")
    assert asked == plain and asked["told"] is None


async def test_long_content_is_cut_and_flagged(
    client: httpx.AsyncClient, registry: dict[str, EvalCard], monkeypatch: pytest.MonkeyPatch
) -> None:
    # No prompt of the registry reaches the cap, so the cap is lowered to one
    # that the longest prompts do: the cut and the flag are what is under test.
    cap = 2000
    monkeypatch.setattr(preview, "CONTENT_LIMIT", cap)
    card_id = "altered_state_meq"
    task = load_task_module(registry[card_id])
    item = task.build_items()[0]
    whole = task.build_messages(item)[0]["content"]
    assert len(whole) > cap

    shown = (await _items(client, card_id, limit=1))["items"][0]
    assert shown["truncated"] is True
    assert shown["messages"] == [{"role": "user", "content": whole[:cap]}]


def test_no_prompt_of_the_real_registry_is_cut(registry: dict[str, EvalCard]) -> None:
    """The cap bounds a response; it must not shorten a prompt a researcher came to read."""
    longest: dict[str, int] = {}
    for card_id in sorted(PREVIEW_SAFE):
        built = build_preview(registry[card_id])
        assert built.listable, f"{card_id}: {built.reason}"
        task = load_task_module(registry[card_id])
        items = task.build_items()
        for told, shown in built.items_by_told.items():
            assert len(shown) == len(items)
            assert not any(item.truncated for item in shown), (card_id, told)
            for item, preview_item in zip(items, shown, strict=True):
                # Whole, to the character: what the preview shows is what is sent.
                assert list(preview_item.messages) == [
                    {"role": m["role"], "content": m["content"]}
                    for m in task.build_messages(item, told)
                ], (card_id, told, preview_item.prompt_id)
            longest[card_id] = max(
                longest.get(card_id, 0),
                max(len(m["content"]) for item in shown for m in item.messages),
            )
    # The longest prompts of the registry are the MEQ ones.
    assert max(longest, key=lambda card_id: longest[card_id]) == "altered_state_meq"
    assert 2000 < longest["altered_state_meq"] < CONTENT_LIMIT
    # A cap all the same: one message can never carry more than this.
    assert CONTENT_LIMIT <= 10_000


async def test_largest_page_of_the_real_registry_is_small(client: httpx.AsyncClient) -> None:
    """At the largest page size no card of the registry answers with more than 256 KiB."""
    largest = 0
    for card_id in sorted(PREVIEW_SAFE):
        first = await client.get(f"{API}/cards/{card_id}/items", params={"limit": ITEMS_MAX_LIMIT})
        assert first.status_code == 200, first.text
        cells = ("told_qrng", "told_prng", "told_nothing") if first.json()["told"] else (None,)
        total = first.json()["total"]
        for told in cells:
            for offset in range(0, total, ITEMS_MAX_LIMIT):
                params = {"limit": ITEMS_MAX_LIMIT, "offset": offset}
                if told is not None:
                    params["told"] = told
                resp = await client.get(f"{API}/cards/{card_id}/items", params=params)
                assert resp.status_code == 200, resp.text
                assert not any(item["truncated"] for item in resp.json()["items"])
                largest = max(largest, len(resp.content))
    assert 64 * 1024 < largest < 256 * 1024


# ── every source ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("card_id", "source"),
    [
        ("source_guessing", "derived_at_run"),
        ("self_reported_coincidence", "derived_at_run"),
        ("target_hit_ranking", "derived_at_run"),
        ("semantic_entropy_sqa", "remote_dataset"),
        ("novelty_bench", "inspect_task"),
        ("tier0_pit_residuals", "no_prompts"),
        ("stream_deviation_windows", "no_prompts"),
    ],
)
async def test_unlistable_sources(client: httpx.AsyncClient, card_id: str, source: str) -> None:
    body = await _items(client, card_id, limit=10, offset=4)
    assert body["source"] == source and body["listable"] is False
    assert body["total"] == 0 and body["items"] == []
    assert (body["offset"], body["limit"]) == (4, 10)
    assert isinstance(body["reason"], str) and body["reason"]
    assert body["told"] is None


_BROKEN_MODULES = {
    "import": 'raise RuntimeError("import blew up")\n',
    "build_items": (
        "def build_items():\n    raise ValueError('bank is short')\n"
        "def build_messages(item, told=None):\n    return []\n"
        "def score(sample):\n    return {}\n"
    ),
    "build_messages": (
        "def build_items():\n    return [{'prompt_id': 'p1'}]\n"
        "def build_messages(item, told=None):\n    return []\n"
        "def score(sample):\n    return {}\n"
    ),
    "duplicate_ids": (
        "def build_items():\n    return [{'prompt_id': 'p1'}, {'prompt_id': 'p1'}]\n"
        "def build_messages(item, told=None):\n    return [{'role': 'user', 'content': 'x'}]\n"
        "def score(sample):\n    return {}\n"
    ),
    "missing": None,
}


@pytest.mark.parametrize("fault", sorted(_BROKEN_MODULES))
async def test_broken_task_module_is_an_error_source_never_a_500(
    settings: Settings, registry: dict[str, EvalCard], tmp_path: Path, fault: str
) -> None:
    card = registry[TOLD_CARD]
    root = tmp_path / "repo"
    assert card.task_module is not None
    module = root / card.task_module
    module.parent.mkdir(parents=True)
    source = _BROKEN_MODULES[fault]
    if source is not None:
        module.write_text(source, encoding="utf-8")

    async for client in _serve(_app(settings, cards={card.id: card}, repo_root=root)):
        body = await _items(client, card.id)
        assert body["source"] == "error" and body["listable"] is False
        assert body["total"] == 0 and body["items"] == []
        assert body["told"] == "told_nothing"
        assert body["reason"]
        # The route is open: the reason names the card, never the server's directories.
        assert str(root) not in body["reason"] and root.as_posix() not in body["reason"]
        # The failure is remembered like a success; the module is not retried per request.
        assert await _items(client, card.id) == body


async def test_repo_card_off_the_list_is_not_imported(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    cards = load_cards(STUB_REGISTRY / "evals")
    assert cards["stub_free"].dataset.scheme == "repo" and "stub_free" not in PREVIEW_SAFE
    loaded: list[str] = []
    monkeypatch.setattr(preview, "load_task_module", lambda card, root=None: loaded.append(card.id))
    async for client in _serve(_app(settings, cards=cards, repo_root=STUB_REGISTRY)):
        body = await _items(client, "stub_free")
    assert body["source"] == "error" and body["listable"] is False
    assert "PREVIEW_SAFE" in body["reason"]
    assert loaded == []


# ── side effects ──────────────────────────────────────────────────────────────


def _refuse(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("a card preview tried to use the network")


def test_previews_build_from_repo_files_without_a_socket(
    registry: dict[str, EvalCard], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every card on PREVIEW_SAFE builds its whole item list with the network cut off."""
    imported: list[str] = []
    real_load: Callable[..., Any] = preview.load_task_module

    def recording_load(card: EvalCard, root: Path | None = None) -> Any:
        imported.append(card.id)
        return real_load(card, root)

    monkeypatch.setattr(preview, "load_task_module", recording_load)
    monkeypatch.setattr(socket, "socket", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    monkeypatch.setattr(urllib.request, "urlopen", _refuse)

    assert set(registry) >= PREVIEW_SAFE
    listable: set[str] = set()
    for card_id, card in sorted(registry.items()):
        built = build_preview(card)
        assert built.source == classify(card), card_id
        # No card of the real registry is left without an answer.
        assert built.source != "error", f"{card_id}: {built.reason}"
        if not built.listable:
            assert built.items_by_told == {} and built.reason
            continue
        listable.add(card_id)
        cells = ("told_qrng", "told_prng", "told_nothing") if card.told_actual else (None,)
        assert tuple(built.items_by_told) == cells
        for cell in cells:
            items = built.items(cell)
            # The list is the card's registered dataset, not a sample of it.
            assert len(items) == card.dataset.items, card_id
            assert [item.index for item in items] == list(range(len(items)))
            assert len({item.prompt_id for item in items}) == len(items)
            for item in items:
                assert item.messages and item.messages[-1]["role"] == "user"
                assert all(len(m["content"]) <= CONTENT_LIMIT for m in item.messages)
    assert listable == set(PREVIEW_SAFE)
    # Only the cleared modules were imported; one that downloads its dataset never is.
    assert set(imported) == set(PREVIEW_SAFE)
    assert "semantic_entropy_sqa" not in imported


async def test_every_card_answers_200_with_the_network_cut_off(
    client: httpx.AsyncClient, registry: dict[str, EvalCard], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The event loop already owns its sockets; what is cut is any new connection.
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    monkeypatch.setattr(urllib.request, "urlopen", _refuse)
    for card_id in sorted(registry):
        body = await _items(client, card_id, limit=1)
        assert body["listable"] is (card_id in PREVIEW_SAFE), card_id
        assert body["source"] != "error", f"{card_id}: {body['reason']}"
        assert len(body["items"]) == (1 if body["listable"] else 0)
        assert len(json.dumps(body)) < 8000


async def test_a_card_is_built_once(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    builds: list[str] = []
    real_build = preview.build_preview

    def counting_build(card: EvalCard, root: Path | None = None) -> Any:
        builds.append(card.id)
        return real_build(card, root)

    monkeypatch.setattr(preview, "build_preview", counting_build)
    pages = [await _items(client, TOLD_CARD, offset=offset, limit=10) for offset in (0, 10, 20)]
    await _items(client, TOLD_CARD, told="told_qrng")
    await _items(client, "tier0_pit_residuals")
    await _items(client, "tier0_pit_residuals")
    assert builds == [TOLD_CARD, "tier0_pit_residuals"]
    assert sum(len(page["items"]) for page in pages) == 30
