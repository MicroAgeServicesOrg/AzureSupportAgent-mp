"""Snapshot-based availability modeling. Provider commitments are not uptime forecasts."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.knowme.sla_catalog import CATALOG_VERSION, ServiceCommitment, resolve_service

logger = logging.getLogger("app.knowme.sla")
CALCULATOR_VERSION = "1"
DISCLAIMER = (
    "Modeled availability under stated assumptions, not a contractual SLA, service-credit "
    "entitlement, observed uptime or a prediction. Service commitments are not measured "
    "failure probabilities. Independent failures are a modeling assumption."
)


class Expression(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operator: Literal["component", "all", "any"]
    component_id: str = Field(default="", max_length=2048)
    children: list[Expression] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def check_shape(self) -> Expression:
        if self.operator == "component":
            if not self.component_id or self.children:
                raise ValueError("A component requires its resource ID and no children.")
            self.component_id = self.component_id.rstrip("/").lower()
        elif self.component_id or len(self.children) < 2:
            raise ValueError("An all/any group requires at least two children and no resource ID.")
        return self


class Exclusion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    component_id: str = Field(min_length=1, max_length=2048)
    reason: str = Field(min_length=1, max_length=1000)


class Journey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    boundary: str = Field(min_length=1, max_length=2000)
    expression: Expression
    exclusions: list[Exclusion] = Field(default_factory=list, max_length=1000)
    independent_failures: Literal[True]
    assumptions: list[Annotated[str, Field(max_length=2000)]] = Field(min_length=1, max_length=30)
    external_dependencies: list[Annotated[str, Field(max_length=2000)]] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def check_limits(self) -> Journey:
        def walk(node: Expression, depth: int) -> int:
            if depth > 8:
                raise ValueError("Availability models support at most eight group levels.")
            return 1 + sum(walk(child, depth + 1) for child in node.children)
        if walk(self.expression, 0) > 128 or len(component_ids(self.expression)) > 16:
            raise ValueError("Availability models support at most 128 nodes and 16 distinct components.")
        if not self.name.strip() or not self.boundary.strip():
            raise ValueError("Journey name and boundary cannot be blank.")
        if any(not item.strip() for item in self.assumptions):
            raise ValueError("Assumptions cannot be blank.")
        if any(not item.reason.strip() for item in self.exclusions):
            raise ValueError("Excluded components require a nonblank reason.")
        if any(not item.strip() for item in self.external_dependencies):
            raise ValueError("External dependency descriptions cannot be blank.")
        excluded = [item.component_id.rstrip("/").lower() for item in self.exclusions]
        if len(set(excluded)) != len(excluded):
            raise ValueError("An excluded component may appear only once.")
        if set(excluded) & component_ids(self.expression):
            raise ValueError("A component cannot be both required and excluded.")
        return self


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    calculator_version: str = CALCULATOR_VERSION
    catalog_version: str = CATALOG_VERSION
    status: Literal["calculated", "needs_review", "incomplete"]
    availability: str | None = None
    percentage: str | None = None
    calculated_at: str
    configuration_observed_at: str
    source_fingerprint: str
    input_fingerprint: str
    inventory_complete: bool
    components: list[ServiceCommitment]
    model: Journey | None = None
    reviewed_by: str = ""
    reviewed_at: str = ""
    unresolved: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def source_fingerprint(arch: dict[str, Any], workload: dict[str, Any] | None) -> str:
    return _fingerprint({
        "architecture_id": arch.get("id"),
        "nodes": [{key: n.get(key) for key in
                   ("id", "arm_id", "type", "sku", "location", "meta")}
                  for n in arch.get("nodes", [])],
        "edges": arch.get("edges", []),
        "workload_id": (workload or {}).get("id"),
        "membership": (workload or {}).get("nodes", []),
        "connection": arch.get("connection_id") or (workload or {}).get("connection_id"),
    })


def component_ids(expression: Expression) -> set[str]:
    if expression.operator == "component":
        return {expression.component_id}
    return set().union(*(component_ids(child) for child in expression.children))


def calculate(expression: Expression, values: dict[str, Decimal]) -> Decimal:
    """Exact Shannon expansion over resource identity, including shared dependencies."""
    tree: Any = _tree(expression)

    def substitute(node: Any, key: str, up: bool) -> Any:
        if isinstance(node, bool):
            return node
        if isinstance(node, str):
            return up if node == key else node
        operator, children = node
        rows = [substitute(child, key, up) for child in children]
        terminal = operator == "any"
        if terminal in rows:
            return terminal
        rows = list(dict.fromkeys(row for row in rows if row is not (not terminal)))
        if not rows:
            return not terminal
        if len(rows) == 1:
            return rows[0]
        return (operator, tuple(rows))

    @lru_cache(maxsize=None)
    def evaluate(node: Any, index: int) -> Decimal:
        if isinstance(node, bool):
            return Decimal(int(node))
        key = keys[index]
        probability = values[key]
        return (probability * evaluate(substitute(node, key, True), index + 1)
                + (1 - probability) * evaluate(substitute(node, key, False), index + 1))

    keys = sorted(component_ids(expression))
    for key in keys:
        if key not in values or not values[key].is_finite() or not 0 <= values[key] <= 1:
            raise ValueError(f"Invalid or missing availability for {key}")
    with localcontext() as context:
        context.prec = 100
        return evaluate(tree, 0)


def _tree(node: Expression) -> Any:
    if node.operator == "component":
        return node.component_id
    return (node.operator, tuple(_tree(child) for child in node.children))


def build_snapshot(
    inventory: dict[str, Any], arch: dict[str, Any], workload: dict[str, Any] | None,
    *, previous: dict[str, Any] | None = None, model: Journey | None = None,
    actor: str = "",
) -> Snapshot:
    components = [resolve_service(row) for row in inventory.get("resources", [])
                  if "/providers/" in str(row.get("id", "")).lower()
                  and str(row.get("type", "")).lower() != "microsoft.resources/resourcegroups"]
    fingerprint = source_fingerprint(arch, workload)
    inputs = _fingerprint([component.model_dump() for component in components])
    reviewed_by = actor if model else ""
    reviewed_at = now() if model else ""
    reasons = []
    if inventory.get("error"):
        reasons.append("Configuration collection failed: " + str(inventory["error"])[:300])
    if not inventory.get("complete"):
        reasons.append("Scoped inventory or SLA configuration collection is incomplete.")
    reasons.extend(str(warning)[:300] for warning in inventory.get("warnings", []))
    if not components:
        reasons.append("No SLA-relevant resource inventory is available.")
    if not model and previous:
        old = Snapshot.model_validate(previous)
        if old.model and old.source_fingerprint == fingerprint:
            model = old.model
            reviewed_by, reviewed_at = old.reviewed_by, old.reviewed_at
        elif old.model:
            reasons.append("Architecture or workload membership changed; review the journey again.")
    all_ids = {component.component_id for component in components}
    if len(all_ids) != len(components):
        reasons.append("Resource identity is duplicated in the inventory.")
    status: Literal["calculated", "needs_review", "incomplete"] = "needs_review"
    if model:
        used = component_ids(model.expression)
        exclusions = {item.component_id.rstrip("/").lower() for item in model.exclusions}
        unknown = (used | exclusions) - all_ids
        unclassified = all_ids - used - exclusions
        if unknown:
            reasons.append("Model references resources missing from the inventory: " + ", ".join(sorted(unknown)))
        if unclassified:
            reasons.append("Classify or explicitly exclude every inventory resource: " + ", ".join(sorted(unclassified)))
        if model.external_dependencies:
            reasons.append("External dependencies are unresolved: " + "; ".join(model.external_dependencies))
        selected = [component for component in components if component.component_id in used]
        for component in selected:
            if component.status != "resolved" or component.availability is None:
                reasons.append(f"{component.name}: {component.reason or component.status}")
        bases = {component.measurement_basis for component in selected if component.status == "resolved"}
        if len(bases) > 1:
            reasons.append("Selected commitments have incompatible measurement bases.")
        status = "incomplete" if reasons else "calculated"
    else:
        reasons.append("Review a critical user journey, its dependencies and exclusions before calculating.")
        if not inventory.get("complete") or not components or inventory.get("error"):
            status = "incomplete"
    availability = None
    percentage = None
    if status == "calculated" and model:
        value = calculate(model.expression, {
            component.component_id: Decimal(component.availability)
            for component in components
            if component.availability is not None and component.component_id in component_ids(model.expression)
        })
        with localcontext() as context:
            context.prec = 100
            availability = format(value.normalize(), "f")
            percentage = format((value * 100).normalize(), "f")
    return Snapshot(
        status=status, availability=availability, percentage=percentage,
        calculated_at=now(), configuration_observed_at=inventory.get("observed_at") or now(),
        source_fingerprint=fingerprint, input_fingerprint=inputs,
        inventory_complete=bool(inventory.get("complete")), components=components, model=model,
        reviewed_by=reviewed_by, reviewed_at=reviewed_at, unresolved=list(dict.fromkeys(reasons)),
    )


async def collect_configuration(workload: dict[str, Any] | None, connection_id: str) -> dict[str, Any]:
    from app.architectures.reverse import _property_id_chunks, collect_workload_inventory
    from app.core.azure_connections import connection_for_workload, get_connection, get_default_connection
    from app.exec.command_runner import close_sp_session, open_sp_session, run_kql_collect

    observed_at = now()
    if not workload:
        return {"resources": [], "complete": False, "error": "A linked workload is required.", "observed_at": observed_at}
    if workload.get("connection_id"):
        connection = connection_for_workload(workload)
    elif connection_id:
        connection = get_connection(connection_id)
        if connection and connection.get("disabled"):
            connection = None
    else:
        connection = get_default_connection()
    if connection is None:
        return {"resources": [], "complete": False,
                "error": "The workload Azure connection is unavailable or disabled.", "observed_at": observed_at}
    inventory = await collect_workload_inventory(workload, connection)
    inventory["observed_at"] = observed_at
    if inventory.get("error"):
        return inventory
    rows = [row for row in inventory.get("resources", []) if "/providers/" in str(row.get("id", "")).lower()]
    if len(rows) > 1000:
        return {**inventory, "complete": False, "error": "SLA collection limit is 1,000 resources; narrow workload scope."}
    config_dir, error = await open_sp_session(connection)
    if error:
        return {**inventory, "complete": False, "error": error}
    enriched: dict[str, dict[str, Any]] = {}
    expected_ids = {str(row["id"]).rstrip("/").lower() for row in rows}
    try:
        for chunk in _property_id_chunks([str(row["id"]) for row in rows]):
            joined = ", ".join("'" + resource_id.replace("'", "''") + "'" for resource_id in chunk)
            result = await asyncio.wait_for(run_kql_collect(
                f"Resources | where id in~ ({joined}) | project id, name, type, location, sku, zones, properties",
                connection, session_config_dir=config_dir, max_rows=len(chunk),
            ), timeout=60)
            if not result.ok or not result.complete:
                return {**inventory, "complete": False, "error": result.error or "Configuration query was incomplete."}
            for row in result.rows:
                resource_id = str(row.get("id", "")).rstrip("/").lower()
                if resource_id not in expected_ids:
                    return {**inventory, "complete": False,
                            "error": "Configuration query returned an out-of-scope resource."}
                enriched[resource_id] = row
    finally:
        close_sp_session(config_dir)
    missing = [row["id"] for row in rows if str(row["id"]).rstrip("/").lower() not in enriched]
    return {**inventory, "resources": list(enriched.values()),
            "complete": bool(inventory.get("complete")) and not missing,
            "warnings": [*inventory.get("warnings", []), *(
                ["Configuration missing for: " + ", ".join(missing)] if missing else [])]}


async def generate_snapshot(
    arch: dict[str, Any], workload: dict[str, Any] | None, connection_id: str,
    *, previous: dict[str, Any] | None = None, model: Journey | None = None, actor: str = "",
) -> dict[str, Any]:
    try:
        inventory = await collect_configuration(workload, connection_id)
    except (RuntimeError, ValueError, OSError, asyncio.TimeoutError) as exc:
        logger.exception("Composite SLA configuration collection failed")
        inventory = {"resources": [], "complete": False, "error": str(exc)[:300], "observed_at": now()}
    snapshot = build_snapshot(inventory, arch, workload, previous=previous, model=model, actor=actor)
    if snapshot.status != "calculated":
        logger.info("Composite SLA withheld: %s", "; ".join(snapshot.unresolved))
    return snapshot.model_dump()


def render_subsection(snapshot: dict[str, Any]) -> str:
    value = Snapshot.model_validate(snapshot)

    def cell(text: str) -> str:
        return text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")

    lines = ["### Composite SLA — modeled availability", "",
             f"**Status:** {value.status.replace('_', ' ')}",
             f"**Snapshot:** {value.configuration_observed_at}", ""]
    if value.percentage is not None:
        lines.append(f"**Modeled availability: {value.percentage}%**")
    else:
        lines.append("**Whole-solution percentage withheld.**")
    lines += ["", value.disclaimer, ""]
    if value.model:
        lines += [f"**Journey:** {cell(value.model.name)}", f"**Boundary:** {cell(value.model.boundary)}",
                  f"**Reviewed by:** {cell(value.reviewed_by)} at {value.reviewed_at}", "",
                  "**Assumptions:**", *[f"- {cell(item)}" for item in value.model.assumptions], ""]
        lines += ["**Dependency expression:** " + _expression_text(value.model.expression), ""]
        if value.model.exclusions:
            lines += ["**Excluded from this journey:**",
                      *[f"- {cell(item.component_id)}: {cell(item.reason)}" for item in value.model.exclusions], ""]
    if value.unresolved:
        lines += ["**Inputs to resolve:**", *[f"- {cell(item)}" for item in value.unresolved], ""]
    lines += ["| Component | Configuration | Commitment | Operation / status | Source |",
              "|---|---|---|---|---|"]
    for component in value.components:
        commitment = format(Decimal(component.availability) * 100, "f") + "%" if component.availability else "Unresolved"
        source = f"[Terms]({component.source_url}) — {cell(component.source_version)}" if component.source_url else "No matching catalog entry"
        lines.append(f"| {cell(component.name)} | {cell(json.dumps(component.configuration, sort_keys=True))} "
                     f"| {commitment} | {cell(component.operation)} / {component.status}: {cell(component.reason)} | {source} |")
    lines += ["", f"Catalog: {value.catalog_version}; calculator: {value.calculator_version}.",
              "Configuration is a point-in-time snapshot, not continuous drift monitoring."]
    return "\n".join(lines)


def _expression_text(node: Expression) -> str:
    if node.operator == "component":
        return node.component_id
    separator = " AND " if node.operator == "all" else " OR "
    return "(" + separator.join(_expression_text(child) for child in node.children) + ")"
