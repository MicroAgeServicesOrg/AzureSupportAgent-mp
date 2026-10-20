"""Background suggestions: real five-slot admission, deduplication and mutation safety."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api import architectures as routes
from app.core.security import Principal
from app.knowme import context, generator, registry, revisions, suggestions


async def test_exactly_five_calls_are_admitted_across_requests():
    active = 0
    peak = 0
    started = []
    release = asyncio.Event()
    five = asyncio.Event()

    async def worker(i):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        started.append(i)
        if len(started) == 5:
            five.set()
        try:
            await release.wait()
            return [str(i)]
        finally:
            active -= 1

    tasks = [asyncio.create_task(suggestions.cached_choices(
        str(i), [], lambda i=i: worker(i))) for i in range(12)]
    await asyncio.wait_for(five.wait(), 2)
    await asyncio.sleep(0)
    assert active == 5 and len(started) == 5
    release.set()
    assert await asyncio.gather(*tasks) == [[str(i)] for i in range(12)]
    assert peak == 5 and active == 0


async def test_duplicate_and_cancelled_subscribers_share_one_call():
    release = asyncio.Event()
    started = asyncio.Event()
    count = 0

    async def worker():
        nonlocal count
        count += 1
        started.set()
        await release.wait()
        return ["30", "60"]

    first = asyncio.create_task(suggestions.cached_choices("same", [], worker))
    await started.wait()
    second = asyncio.create_task(suggestions.cached_choices("same", [], worker))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second == ["30", "60"]
    assert await suggestions.cached_choices("same", [], worker) == ["30", "60"]
    assert count == 1


async def test_actual_provider_streams_never_exceed_five(monkeypatch):
    active = 0
    peak = 0
    calls = 0
    five = asyncio.Event()
    release = asyncio.Event()

    class Provider:
        async def stream(self, *args, **kwargs):
            nonlocal active, peak, calls
            active += 1
            calls += 1
            peak = max(peak, active)
            if calls == 5:
                five.set()
            try:
                await release.wait()
                yield SimpleNamespace(type="token", text='["30","60"]')
            finally:
                active -= 1

    monkeypatch.setattr(generator, "build_provider", Provider)
    tasks = [asyncio.create_task(generator.suggest_field_choices(
        label=f"Threshold {i}", cache_scope=f"scope{i}")) for i in range(11)]
    await asyncio.wait_for(five.wait(), 2)
    assert calls == 5 and active == 5
    release.set()
    assert await asyncio.gather(*tasks) == [["30", "60"]] * 11
    assert peak == 5 and calls == 11 and active == 0


async def test_cache_is_bounded_and_expiry_recomputes(monkeypatch):
    monkeypatch.setattr(suggestions, "CACHE_LIMIT", 2)
    worker = AsyncMock(return_value=["cached"])
    for scope in ("one", "two", "three"):
        await suggestions.cached_choices(scope, [], worker)
    await suggestions.cached_choices("one", [], worker)
    assert worker.await_count == 4
    monkeypatch.setattr(suggestions, "CACHE_TTL", -1)
    await suggestions.cached_choices("one", [], worker)
    assert worker.await_count == 5


async def test_empty_results_cache_but_errors_retry():
    empty = AsyncMock(return_value=[])
    assert await suggestions.cached_choices("empty", [], empty) == []
    assert await suggestions.cached_choices("empty", [], empty) == []
    assert empty.await_count == 1
    failure = AsyncMock(side_effect=[RuntimeError("provider unavailable"), ["fixed"]])
    with pytest.raises(RuntimeError):
        await suggestions.cached_choices("failure", [], failure)
    assert await suggestions.cached_choices("failure", [], failure) == ["fixed"]
    assert failure.await_count == 2


async def test_cache_scope_and_inputs_are_independent():
    worker = AsyncMock(return_value=["choice"])
    for scope, inputs in [("tenant-a", ["old"]), ("tenant-b", ["old"]), ("tenant-a", ["new"])]:
        assert await suggestions.cached_choices(scope, inputs, worker) == ["choice"]
    assert worker.await_count == 3


@pytest.mark.parametrize("payload", ["not JSON", '[{"value":"bad"}]', '["unterminated]'])
async def test_invalid_provider_response_is_explicit_and_provider_closes(monkeypatch, payload):
    closed = []

    class Provider:
        async def stream(self, *args, **kwargs):
            yield SimpleNamespace(type="token", text=payload)

        def close(self):
            closed.append(True)

    monkeypatch.setattr(generator, "build_provider", Provider)
    with pytest.raises(suggestions.SuggestionError):
        await generator.suggest_field_choices(label="Threshold", cache_scope=payload)
    assert closed == [True]


async def test_valid_provider_empty_response_and_deduplication(monkeypatch):
    calls = []

    class Provider:
        async def stream(self, *args, **kwargs):
            calls.append(True)
            yield SimpleNamespace(type="token", text='["30", " 60 ", "30", ""]')

    monkeypatch.setattr(generator, "build_provider", Provider)
    assert await generator.suggest_field_choices(label="Threshold", cache_scope="one") == ["30", "60"]
    assert await generator.suggest_field_choices(label="Threshold", cache_scope="one") == ["30", "60"]
    assert calls == [True]


@pytest.fixture
def document(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "_PATH", tmp_path / "documents.json")
    monkeypatch.setattr(revisions, "_PATH", tmp_path / "revisions.json")
    arch = {"id": "arch", "tenant_id": "tenant", "updated_at": "one"}
    monkeypatch.setattr(routes.arch_registry, "get_architecture", lambda *args, **kwargs: arch)
    monkeypatch.setattr(routes, "get_workload", lambda *args: None)
    monkeypatch.setattr(context, "gather_known_facts", AsyncMock(return_value={}))
    monkeypatch.setattr(context, "gather_evidence", AsyncMock(return_value={}))
    fields = [{"id": f"thresholds_slis:f{i}", "field_key": f"f{i}", "section_key": "thresholds_slis",
               "label": f"Threshold {i}", "type": "number", "value": "", "status": "open"}
              for i in range(7)]
    doc = registry.create_know_me(architecture_id="arch", tenant_id="tenant")
    doc = registry.update_know_me(doc["id"], todos=fields)
    principal = Principal(subject="tester", email="test@example.invalid", tenant_id="tenant", role="admin")
    return doc, principal


async def test_preload_does_not_modify_fields_or_revision_history(document, monkeypatch):
    doc, principal = document
    before = deepcopy(registry.get_know_me(doc["id"]))
    history = revisions.list_revisions(doc["id"])
    monkeypatch.setattr(generator, "suggest_field_choices", AsyncMock(return_value=["30", "60"]))
    responses = await asyncio.gather(*[
        routes.suggest_know_me_field_endpoint(doc["id"], field["id"], principal, preload=True)
        for field in doc["todos"]])
    assert all(response["choices"] == ["30", "60"] for response in responses)
    assert registry.get_know_me(doc["id"]) == before
    assert revisions.list_revisions(doc["id"]) == history


async def test_concurrent_legacy_suggestions_preserve_all_fields_and_manual_edits(document, monkeypatch):
    doc, principal = document
    gate = asyncio.Event()
    started = asyncio.Event()
    count = 0

    async def generate(**kwargs):
        nonlocal count
        count += 1
        if count == 7:
            started.set()
        await gate.wait()
        return [kwargs["label"]]

    monkeypatch.setattr(generator, "suggest_field_choices", generate)
    tasks = [asyncio.create_task(routes.suggest_know_me_field_endpoint(
        doc["id"], field["id"], principal, preload=False)) for field in doc["todos"]]
    await started.wait()
    fields = deepcopy(registry.get_know_me(doc["id"])["todos"])
    fields[0].update(value="75", status="done", assignee="human@example.invalid")
    registry.update_know_me(doc["id"], todos=fields)
    gate.set()
    await asyncio.gather(*tasks)
    saved = registry.get_know_me(doc["id"])["todos"]
    assert all(field["choices"] == [field["label"]] for field in saved)
    assert saved[0]["value"] == "75" and saved[0]["status"] == "done"
    assert saved[0]["assignee"] == "human@example.invalid"


async def test_existing_platform_choices_skip_ai(document, monkeypatch):
    doc, principal = document
    fields = deepcopy(doc["todos"])
    fields[0].update(choices=["Known"], choice_source="platform")
    registry.update_know_me(doc["id"], todos=fields)
    model = AsyncMock()
    monkeypatch.setattr(generator, "suggest_field_choices", model)
    response = await routes.suggest_know_me_field_endpoint(doc["id"], fields[0]["id"], principal, preload=True)
    assert response == {"choices": ["Known"], "choice_source": "platform"}
    model.assert_not_awaited()


async def test_generation_failures_surface_not_success_shaped_empty_arrays(document, monkeypatch):
    doc, principal = document
    monkeypatch.setattr(generator, "suggest_field_choices", AsyncMock(side_effect=RuntimeError("offline")))
    with pytest.raises(HTTPException) as error:
        await routes.suggest_know_me_field_endpoint(doc["id"], doc["todos"][0]["id"], principal, preload=True)
    assert error.value.status_code == 502


async def test_suggestions_accept_authorized_azure_workload_tenant(document, monkeypatch):
    doc, principal = document
    monkeypatch.setattr(routes, "get_workload", lambda *args: {
        "id": "azure-workload", "tenant_id": "azure-directory", "name": "Shared Azure workload",
    })
    monkeypatch.setattr(generator, "suggest_field_choices", AsyncMock(return_value=["80"]))
    response = await routes.suggest_know_me_field_endpoint(
        doc["id"], doc["todos"][0]["id"], principal, preload=True)
    assert response["choices"] == ["80"]


async def test_changed_field_rejects_late_suggestions(document, monkeypatch):
    doc, principal = document

    async def generate(**kwargs):
        fields = deepcopy(registry.get_know_me(doc["id"])["todos"])
        fields[0]["label"] = "Changed threshold"
        registry.update_know_me(doc["id"], todos=fields)
        return ["old"]

    monkeypatch.setattr(generator, "suggest_field_choices", generate)
    with pytest.raises(HTTPException) as error:
        await routes.suggest_know_me_field_endpoint(doc["id"], doc["todos"][0]["id"], principal, preload=True)
    assert error.value.status_code == 409
    assert not registry.get_know_me(doc["id"])["todos"][0]["choices"]
