"""Isolated calculation, persistence, API and generation acceptance tests."""
from copy import deepcopy
from decimal import Decimal
from itertools import product
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import architectures as routes
from app.architectures import memory
from app.core.security import Principal
from app.knowme import context, generator, registry, revisions, sections, sla
from app.knowme.sla_catalog import ServiceCommitment

PREFIX = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/test/providers/Microsoft.Test/services/"


def leaf(name):
    return {"operator": "component", "component_id": PREFIX.lower() + name}


def group(operator, *nodes):
    return {"operator": operator, "children": list(nodes)}


def journey(expression=None, **kwargs):
    return sla.Journey.model_validate({
        "name": "Accept an order", "boundary": "Successful durable order acceptance",
        "expression": expression or group("all", leaf("a"), leaf("b")),
        "independent_failures": True,
        "assumptions": ["Failures are modeled as independent; this is not observed uptime."],
        **kwargs,
    })


def resource(name, availability="0.999", status="resolved"):
    return {"id": PREFIX + name, "name": name, "type": "Microsoft.Test/services",
            "location": "eastus", "sku": {"name": "Standard"},
            "availability": availability, "status": status}


def resolve(row):
    return ServiceCommitment(
        component_id=row["id"].lower(), resource_id=row["id"], name=row["name"],
        resource_type=row["type"], location=row["location"], sku="Standard",
        configuration={}, status=row.get("status", "resolved"),
        availability=row.get("availability"), operation="synthetic request availability",
        source_url="https://learn.microsoft.com/en-us/azure/well-architected/reliability/metrics",
        source_version="synthetic-test", rule_id="synthetic-test",
        measurement_basis=row.get("measurement_basis", "monthly availability"),
        reason="" if row.get("status", "resolved") == "resolved" else "Configuration unavailable",
    )


@pytest.fixture
def environment(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "_PATH", tmp_path / "know_me.json")
    monkeypatch.setattr(revisions, "_PATH", tmp_path / "know_me_revisions.json")
    monkeypatch.setattr(sla, "resolve_service", resolve)
    arch = {"id": "arch", "tenant_id": "tenant", "workload_id": "wl",
            "connection_id": "conn", "nodes": [], "edges": []}
    wl = {"id": "wl", "tenant_id": "tenant", "name": "Synthetic orders", "nodes": []}
    inventory = {"resources": [resource("a"), resource("b", "0.9995")],
                 "complete": True, "warnings": [], "observed_at": "2026-10-09T12:00:00Z"}
    monkeypatch.setattr(routes.arch_registry, "get_architecture", lambda *a, **kw: arch)
    monkeypatch.setattr(routes.arch_registry, "list_architectures", lambda *a, **kw: [arch])
    monkeypatch.setattr(routes, "get_workload", lambda *a: wl)
    monkeypatch.setattr(memory, "get_memory", lambda *a: {
        "sections": [{"key": "overview", "content": "Synthetic order service"}]})
    monkeypatch.setattr(sla, "collect_configuration", AsyncMock(side_effect=lambda *a: deepcopy(inventory)))
    monkeypatch.setattr(context, "gather_known_facts", AsyncMock(return_value={}))
    monkeypatch.setattr(context, "gather_evidence", AsyncMock(return_value={}))
    monkeypatch.setattr(generator, "generate_know_me", AsyncMock(return_value={
        "sections": {"overview": "Synthetic workload", "resiliency_dr": "Customer SLA: ⟦TODO: Contract SLA | key=contract_sla⟧"},
        "passes": 2, "confidence": 0.8,
    }))
    monkeypatch.setattr(generator, "generate_section", AsyncMock(return_value="Reviewed human resiliency prose"))
    principal = Principal(subject="test", email="test@example.invalid", tenant_id="tenant",
                          role="admin", display_name="Tester")
    doc = registry.create_know_me(architecture_id="arch", workload_id="wl", tenant_id="tenant")
    return SimpleNamespace(arch=arch, wl=wl, inventory=inventory, principal=principal, doc=doc)


@pytest.mark.parametrize(("expression", "values", "expected"), [
    (group("all", leaf("a"), leaf("b")), {"a": "0.999", "b": "0.9995"}, "0.9985005"),
    (group("any", leaf("a"), leaf("b")), {"a": "0.999", "b": "0.999"}, "0.999999"),
    (group("all", leaf("a"), group("any", leaf("b"), leaf("c"))),
     {"a": "0.999", "b": "0.999", "c": "0.999"}, "0.998999001"),
    (group("any", group("all", leaf("a"), leaf("b")), group("all", leaf("a"), leaf("c"))),
     {"a": "0.999", "b": "0.999", "c": "0.999"}, "0.998999001"),
    (group("all", leaf("a"), leaf("a")), {"a": "0.999"}, "0.999"),
    (group("any", leaf("a"), leaf("a")), {"a": "0.999"}, "0.999"),
    (group("all", leaf("a"), leaf("b")), {"a": "0", "b": "1"}, "0"),
    (group("any", leaf("a"), leaf("b")), {"a": "0", "b": "1"}, "1"),
])
def test_exact_arithmetic(expression, values, expected):
    value = sla.calculate(sla.Expression.model_validate(expression), {
        PREFIX.lower() + key: Decimal(probability) for key, probability in values.items()})
    assert value == Decimal(expected)


def test_shared_dependency_models_match_independent_truth_table_oracle():
    probabilities = {"a": Decimal("0.999"), "b": Decimal("0.9995"), "c": Decimal("0.99")}
    expressions = []
    for outer in ("all", "any"):
        for inner in ("all", "any"):
            expressions += [
                group(outer, group(inner, leaf("a"), leaf("b")),
                      group(inner, leaf("a"), leaf("c"))),
                group(outer, leaf("a"), group(inner, leaf("a"), leaf("b"), leaf("c"))),
            ]

    def succeeds(node, up):
        if node["operator"] == "component":
            return up[node["component_id"].rsplit("/", 1)[-1]]
        outcomes = [succeeds(child, up) for child in node["children"]]
        return all(outcomes) if node["operator"] == "all" else any(outcomes)

    for expression in expressions:
        expected = Decimal(0)
        for states in product((False, True), repeat=3):
            up = dict(zip(probabilities, states))
            weight = Decimal(1)
            for key, value in probabilities.items():
                weight *= value if up[key] else 1 - value
            if succeeds(expression, up):
                expected += weight
        actual = sla.calculate(sla.Expression.model_validate(expression), {
            PREFIX.lower() + key: value for key, value in probabilities.items()})
        assert actual == expected


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-0.1", "1.1"])
def test_invalid_probability_rejected(value):
    with pytest.raises(ValueError, match="Invalid or missing"):
        sla.calculate(sla.Expression.model_validate(leaf("a")), {PREFIX.lower() + "a": Decimal(value)})


@pytest.mark.parametrize("changes", [
    {"independent_failures": False}, {"assumptions": [" "]}, {"name": " "},
    {"expression": {"operator": "component", "component_id": ""}},
    {"expression": group("all", leaf("a"))},
    {"exclusions": [{"component_id": PREFIX + "a", "reason": "already used"}]},
    {"expression": group("all", *[leaf(str(i)) for i in range(17)])},
])
def test_invalid_model_rejected(changes):
    with pytest.raises(ValidationError):
        journey(**changes)


def test_snapshot_review_and_preserved_human_fields(environment):
    env = environment
    withheld = sla.build_snapshot(env.inventory, env.arch, env.wl)
    assert withheld.status == "needs_review" and withheld.percentage is None
    calculated = sla.build_snapshot(env.inventory, env.arch, env.wl, model=journey(), actor="Tester")
    assert calculated.percentage == "99.85005"
    assert calculated.reviewed_by == "Tester"
    assert calculated.inventory_complete
    assert "prediction" in calculated.disclaimer


@pytest.mark.parametrize("mutation", [
    "partial", "missing", "unsupported", "ineligible", "duplicate",
    "external", "unknown", "unclassified", "basis",
])
def test_incomplete_inputs_never_produce_percentage(environment, mutation):
    env = environment
    model = journey()
    if mutation == "partial":
        env.inventory["complete"] = False
    elif mutation == "missing":
        env.inventory["resources"][1]["status"] = "unresolved"
        env.inventory["resources"][1]["availability"] = None
    elif mutation in ("unsupported", "ineligible"):
        env.inventory["resources"][1]["status"] = mutation
        env.inventory["resources"][1]["availability"] = None
    elif mutation == "duplicate":
        env.inventory["resources"].append(resource("a"))
    elif mutation == "external":
        model = journey(external_dependencies=["On-premise identity provider"])
    elif mutation == "unknown":
        model = journey(group("all", leaf("a"), leaf("missing")))
    elif mutation == "unclassified":
        env.inventory["resources"].append(resource("c"))
    elif mutation == "basis":
        env.inventory["resources"][1]["measurement_basis"] = "request success"
    snapshot = sla.build_snapshot(env.inventory, env.arch, env.wl, model=model, actor="Tester")
    assert snapshot.status == "incomplete"
    assert snapshot.percentage is None and snapshot.availability is None and snapshot.unresolved


def test_exclusion_is_explicit_and_configuration_change_recalculates(environment):
    env = environment
    env.inventory["resources"].append(resource("optional", None, "unsupported"))
    model = journey(exclusions=[{"component_id": PREFIX + "optional", "reason": "Async reporting outside order acceptance"}])
    first = sla.build_snapshot(env.inventory, env.arch, env.wl, model=model, actor="Tester")
    assert first.status == "calculated"
    env.inventory["resources"][0]["availability"] = "0.9999"
    second = sla.build_snapshot(env.inventory, env.arch, env.wl, previous=first.model_dump())
    assert second.status == "calculated" and second.percentage != first.percentage
    assert second.input_fingerprint != first.input_fingerprint
    assert second.reviewed_at == first.reviewed_at


def test_architecture_change_requires_new_review(environment):
    env = environment
    first = sla.build_snapshot(env.inventory, env.arch, env.wl, model=journey(), actor="Tester")
    env.arch["edges"] = [{"source": "a", "target": "new", "kind": "depends_on"}]
    snapshot = sla.build_snapshot(env.inventory, env.arch, env.wl, previous=first.model_dump())
    assert snapshot.status == "needs_review" and snapshot.model is None
    assert any("changed" in reason for reason in snapshot.unresolved)


def test_history_exports_and_restore_are_snapshot_based(environment):
    env = environment
    kid = env.doc["id"]
    registry.update_know_me(kid, sections=sections.default_sections())
    old_revision = revisions.list_revisions(kid)[0]["id"]
    first = sla.build_snapshot(env.inventory, env.arch, env.wl, model=journey(), actor="Tester").model_dump()
    saved = registry.update_know_me(kid, composite_sla=first)
    calculation_revision = revisions.list_revisions(kid)[0]["id"]
    assert saved["composite_sla"] == first
    md = sections.render_markdown(saved, "Orders", cover=True)
    assert "Composite SLA" in md and "99.85005%" in md
    assert len(saved["sections"]) == 13
    second = {**first, "configuration_observed_at": "2026-10-10T12:00:00Z"}
    registry.update_know_me(kid, composite_sla=second)
    assert len(revisions.list_revisions(kid)) == 3
    restored = registry.restore_revision(kid, calculation_revision)
    assert restored["composite_sla"] == first
    legacy = registry.restore_revision(kid, old_revision)
    assert legacy.get("composite_sla") is None
    assert "Composite SLA" not in sections.render_markdown(legacy)


def test_legacy_revision_signature_does_not_change_without_snapshot():
    old_content = {"title": "Legacy reference", "sections": [], "todos": [], "status": "draft"}
    assert json.loads(revisions.signature(old_content)) == old_content
    assert revisions.signature({**old_content, "composite_sla": None}) == revisions.signature(old_content)


def test_snapshot_export_exists_without_human_resiliency_section(environment):
    env = environment
    snapshot = sla.build_snapshot(env.inventory, env.arch, env.wl, model=journey(), actor="Tester")
    markdown = sections.render_markdown({"sections": [], "composite_sla": snapshot.model_dump()})
    assert "## Resiliency & DR posture" in markdown and "99.85005%" in markdown


async def test_generate_and_regenerate_pipeline(environment):
    env = environment
    progress = AsyncMock()
    response = await routes._run_generate_know_me(
        progress, architecture_id="arch", arch=env.arch, principal=env.principal,
        extra_context="", target_km_id=env.doc["id"],
    )
    doc = response["know_me"]
    assert doc["composite_sla"]["status"] == "needs_review"
    assert any(todo["field_key"] == "contract_sla" and todo["status"] == "open" for todo in doc["todos"])
    response = await routes.review_composite_sla_endpoint(
        doc["id"], routes.CompositeSlaReviewRequest(model=journey().model_dump()), env.principal,
    )
    assert response["know_me"]["composite_sla"]["status"] == "calculated"
    assert response["composite_sla_stale"] is False
    assert response["has_memory"] is True
    response = await routes._run_generate_know_me(
        progress, architecture_id="arch", arch=env.arch, principal=env.principal,
        extra_context="", target_km_id=doc["id"],
    )
    assert response["know_me"]["composite_sla"]["status"] == "calculated"
    assert response["has_memory"] is True
    assert generator.generate_know_me.await_count == 2
    assert "composite_sla" not in generator.generate_know_me.await_args.kwargs
    assert any(call.args[0] == "sla" for call in progress.await_args_list)


async def test_build_from_workload_pipeline(environment):
    env = environment
    response = await routes._run_know_me_from_workload(
        AsyncMock(), workload_id="wl", explicit_arch_id="arch", extra_context="",
        connection_id="conn", tenant_id="tenant", principal=env.principal,
    )
    assert response["know_me"]["composite_sla"]["status"] == "needs_review"
    assert len(response["know_me"]["sections"]) == 13


async def test_relevant_section_refreshes_snapshot_other_sections_preserve(environment):
    env = environment
    reviewed = await routes.review_composite_sla_endpoint(
        env.doc["id"], routes.CompositeSlaReviewRequest(model=journey().model_dump()), env.principal)
    first = reviewed["know_me"]["composite_sla"]
    for key in ["overview", "resiliency_dr"]:
        env.inventory["resources"][0]["availability"] = "0.9999"
        response = await routes.generate_know_me_section_stream_endpoint(
            env.doc["id"], key, routes.KnowMeSectionGenerateRequest(), env.principal)
        events = [event async for event in response.body_iterator]
        assert events[-1]["event"] == "done"
        saved = registry.get_know_me(env.doc["id"])
        if key == "overview":
            assert saved["composite_sla"] == first
        else:
            assert saved["composite_sla"]["percentage"] != first["percentage"]


async def test_collection_failure_does_not_block_document(environment, monkeypatch):
    monkeypatch.setattr(sla, "collect_configuration", AsyncMock(side_effect=TimeoutError("Synthetic timeout")))
    env = environment
    response = await routes._run_generate_know_me(
        AsyncMock(), architecture_id="arch", arch=env.arch, principal=env.principal,
        extra_context="", target_km_id=env.doc["id"])
    snapshot = response["know_me"]["composite_sla"]
    assert snapshot["status"] == "incomplete" and snapshot["percentage"] is None
    assert any("timeout" in issue for issue in snapshot["unresolved"])


async def test_missing_document_at_generation_save_surfaces_error(environment, monkeypatch):
    env = environment
    monkeypatch.setattr(registry, "update_know_me", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="removed while generation"):
        await routes._run_generate_know_me(
            AsyncMock(), architecture_id="arch", arch=env.arch, principal=env.principal,
            extra_context="", target_km_id=env.doc["id"])


async def test_review_rejects_source_change_during_collection(environment, monkeypatch):
    from fastapi import HTTPException

    env = environment
    calls = 0

    def architecture(*args, **kwargs):
        nonlocal calls
        calls += 1
        return env.arch if calls == 1 else {**env.arch, "edges": [{"source": "changed", "target": "scope"}]}

    monkeypatch.setattr(routes.arch_registry, "get_architecture", architecture)
    with pytest.raises(HTTPException) as error:
        await routes.review_composite_sla_endpoint(
            env.doc["id"], routes.CompositeSlaReviewRequest(model=journey().model_dump()), env.principal)
    assert error.value.status_code == 409
    assert registry.get_know_me(env.doc["id"]).get("composite_sla") is None


async def test_exports_and_known_staleness(environment):
    env = environment
    await routes.review_composite_sla_endpoint(
        env.doc["id"], routes.CompositeSlaReviewRequest(model=journey().model_dump()), env.principal)
    env.arch["nodes"].append({"id": "new", "type": "new"})
    response = await routes.get_know_me_endpoint(env.doc["id"], env.principal)
    assert response["composite_sla_stale"] and "Stale snapshot" in response["markdown"]
    export = await routes.export_know_me_endpoint(env.doc["id"], "md", env.principal)
    assert b"Stale snapshot" in export.body and b"99.85005%" in export.body
    pdf = await routes.export_know_me_endpoint(env.doc["id"], "pdf", env.principal)
    assert pdf.body.startswith(b"%PDF")


def test_review_api_enforces_permissions_and_tenant(environment):
    from app.core import security

    app = FastAPI()
    app.include_router(routes.router)
    env = environment
    app.dependency_overrides[security.get_principal] = lambda: Principal(
        subject="reader", email="reader@example.invalid", tenant_id="tenant", role="user",
        permissions=frozenset({"architectures.read"}))
    with TestClient(app) as client:
        path = f"/architectures/know-me/{env.doc['id']}/composite-sla"
        assert client.post(path, json={}).status_code == 403
        app.dependency_overrides[security.get_principal] = lambda: env.principal
        response = client.post(path, json={"model": journey().model_dump()})
        assert response.status_code == 200
        assert response.json()["know_me"]["composite_sla"]["status"] == "calculated"
        assert client.post(path, json={"model": {"name": "invalid"}}).status_code == 422
        env.doc["tenant_id"] = "other"
        monkey_document = registry.get_know_me(env.doc["id"])
        # Patch the persisted tenant through the registry rather than bypassing the guard.
        registry.update_know_me(monkey_document["id"], tenant_id="other")
        assert client.post(path, json={}).status_code == 404


@pytest.mark.parametrize("mode", ["complete", "partial", "failure", "missing", "outside"])
async def test_collector_completeness_and_scope(monkeypatch, mode):
    from app.architectures import reverse
    from app.core import azure_connections
    from app.exec import command_runner

    monkeypatch.setattr(azure_connections, "get_connection", lambda *args: {"id": "conn"})
    inventory = {"resources": [resource("a")], "complete": mode != "partial", "warnings": []}
    monkeypatch.setattr(reverse, "collect_workload_inventory", AsyncMock(return_value=inventory))
    result = SimpleNamespace(
        ok=mode != "failure", complete=mode != "partial", error="failure" if mode == "failure" else "",
        rows=[] if mode == "missing" else [resource("outside" if mode == "outside" else "a")])
    monkeypatch.setattr(command_runner, "open_sp_session", AsyncMock(return_value=("isolated", "")))
    monkeypatch.setattr(command_runner, "run_kql_collect", AsyncMock(return_value=result))
    close = []
    monkeypatch.setattr(command_runner, "close_sp_session", close.append)
    collected = await sla.collect_configuration({"id": "wl", "nodes": []}, "conn")
    assert collected["complete"] is (mode == "complete")
    assert close == ["isolated"]
    if mode in ("failure", "outside", "partial"):
        assert collected["error"]
    if mode == "missing":
        assert collected["warnings"]


async def test_collector_does_not_fallback_from_invalid_connection(monkeypatch):
    from app.core import azure_connections

    monkeypatch.setattr(azure_connections, "get_connection", lambda *args: None)
    monkeypatch.setattr(azure_connections, "get_default_connection", lambda: pytest.fail("Must not fall back"))
    result = await sla.collect_configuration({"id": "wl", "connection_id": "missing"}, "missing")
    assert result["complete"] is False and "connection" in result["error"]
