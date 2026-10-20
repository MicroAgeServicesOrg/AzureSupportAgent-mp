"""Independent expectations from Microsoft Online Services SLA, October 1, 2026.

Primary clauses/URLs are recorded in app.knowme.sla_catalog; retrieved 2026-10-09.
These fixtures describe eligibility explicitly, not live Azure/default configurations.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.knowme.sla_catalog import CATALOG_VERSION, ServiceCommitment, resolve_service

PRIMARY_URL = (
    "https://www.microsoft.com/licensing/docs/documents/download/"
    "OnlineSvcsConsolidatedSLA(WW)(English)(October_2026)(CR).docx"
)


def row(resource_type: str, **kwargs: Any) -> dict[str, Any]:
    provider, resource_path = resource_type.split("/", 1)
    value = {
        "id": f"/subscriptions/SUB/resourceGroups/RG/providers/{provider}/{resource_path}/Example",
        "name": "Example",
        "type": resource_type,
        "location": "eastus",
        "properties": {},
    }
    value.update(kwargs)
    return value


def storage(operation: str = "blob_hot_write", sku: str = "Standard_LRS") -> dict[str, Any]:
    return row(
        "Microsoft.Storage/storageAccounts",
        kind="StorageV2",
        sku={"name": sku, "tier": "Standard"},
        operation=operation,
    )


def app(zonal: bool = False) -> dict[str, Any]:
    return row(
        "Microsoft.Web/serverfarms",
        sku={"name": "P1v3", "tier": "PremiumV3", "capacity": 2},
        properties={"zoneRedundant": zonal},
    )


def vm(os_type: str = "Premium_LRS", data_types: tuple[str, ...] = ()) -> dict[str, Any]:
    return row(
        "Microsoft.Compute/virtualMachines",
        properties={
            "priority": "Regular",
            "availabilitySet": None,
            "storageProfile": {
                "osDisk": {"managedDisk": {"storageAccountType": os_type}},
                "dataDisks": [
                    {"managedDisk": {"storageAccountType": t, "maxShares": 1}} for t in data_types
                ],
            },
        },
    )


def sql(zonal: bool = False) -> dict[str, Any]:
    return row(
        "Microsoft.Sql/servers/databases",
        sku={"name": "GP_Gen5", "tier": "GeneralPurpose"},
        properties={"zoneRedundant": zonal},
    )


def cosmos(zonal: bool = False) -> dict[str, Any]:
    return row(
        "Microsoft.DocumentDB/databaseAccounts",
        kind="GlobalDocumentDB",
        properties={
            "capabilities": [],
            "capacityMode": "Provisioned",
            "enableMultipleWriteLocations": False,
            "consistencyPolicy": {"defaultConsistencyLevel": "Session"},
            "locations": [{"locationName": "eastus", "isZoneRedundant": zonal}],
        },
    )


def kv(name: str = "standard") -> dict[str, Any]:
    return row("Microsoft.KeyVault/vaults", sku={"name": name, "family": "A"})


def messaging(family: str, tier: str = "Standard", **kwargs: Any) -> dict[str, Any]:
    return row(f"Microsoft.{family}/namespaces", sku={"name": tier, "tier": tier}, **kwargs)


def aks(tier: str = "Standard", location: str = "eastus") -> dict[str, Any]:
    return row(
        "Microsoft.ContainerService/managedClusters",
        sku={"name": "Base", "tier": tier},
        location=location,
    )


# Literal expected rule IDs/rates, not values derived from the implementation catalog.
RULE_CASES = [
    (storage(), "storage.blob.hot.write", "0.999"),
    (storage("blob_hot_read"), "storage.blob.hot.read", "0.999"),
    (storage("blob_cool_write"), "storage.blob.cool.write", "0.99"),
    (storage("blob_cool_read"), "storage.blob.cool.read", "0.999"),
    (storage("blob_cold_write"), "storage.blob.cold.write", "0.99"),
    (storage("blob_cold_read"), "storage.blob.cold.read", "0.999"),
    (storage("blob_archive_write"), "storage.blob.archive.write", "0.99"),
    (app(), "app-service.regional", "0.9995"),
    (app(True), "app-service.zone-redundant", "0.9999"),
    (vm(), "vm.single.premium", "0.999"),
    (vm("StandardSSD_LRS"), "vm.single.standard-ssd", "0.995"),
    (vm("Standard_LRS"), "vm.single.standard-hdd", "0.95"),
    (sql(), "sql.regional", "0.9999"),
    (sql(True), "sql.zone-redundant", "0.99995"),
    (cosmos(), "cosmos.single-region", "0.9999"),
    (cosmos(True), "cosmos.single-region-zonal", "0.99995"),
    (kv(), "key-vault.vault", "0.9999"),
    (
        messaging("ServiceBus", location="westus"),
        "service-bus.non-az-region",
        "0.999",
    ),
    (
        messaging("ServiceBus", "Premium", properties={"premiumMessagingPartitions": 2}),
        "service-bus.premium.partitioned-az",
        "0.9999",
    ),
    (messaging("EventHub"), "event-hubs.basic-standard", "0.9995"),
    (messaging("EventHub", "Premium"), "event-hubs.premium", "0.9999"),
    (aks(), "aks.control-plane.zonal", "0.9995"),
    (aks(location="westus"), "aks.control-plane.regional", "0.999"),
]


@pytest.mark.parametrize(
    "resource,rule_id,availability", RULE_CASES, ids=[c[1] for c in RULE_CASES]
)
def test_every_supported_rule_has_independent_rate_and_primary_clause(
    resource: dict[str, Any],
    rule_id: str,
    availability: str,
) -> None:
    value = resolve_service(deepcopy(resource))
    assert value.status == "resolved"
    assert value.availability == availability
    assert value.rule_id == rule_id
    assert value.source_url == PRIMARY_URL
    assert "October 1, 2026" in value.source_version
    assert "2026-10-09" in value.source_version
    assert value.operation and value.measurement_basis and "< " in value.reason
    assert value.assumptions
    assert ServiceCommitment.model_validate(value.model_dump()) == value


def test_all_catalog_rules_have_a_test_and_nine_real_families_are_covered() -> None:
    # Coverage assertion only; expected percentages above remain independent literals.
    from app.knowme.sla_catalog import _RULES

    assert {c[1] for c in RULE_CASES} == set(_RULES)
    assert len({c[1].split(".")[0] for c in RULE_CASES}) == 9
    assert CATALOG_VERSION == "2026-10-09.1"


def assert_no_rate(resource: dict[str, Any], status: str, reason: str = "") -> ServiceCommitment:
    value = resolve_service(resource)
    assert value.status == status
    assert value.availability is None
    assert value.rule_id == ""
    assert value.reason
    if reason:
        assert reason.lower() in value.reason.lower()
    return value


@pytest.mark.parametrize("field", ["id", "name", "type", "location"])
def test_missing_identity_is_not_fabricated(field: str) -> None:
    resource = kv()
    resource.pop(field)
    value = assert_no_rate(resource, "unresolved")
    output_field = {"id": "resource_id", "type": "resource_type"}.get(field, field)
    assert getattr(value, output_field) == ""


@pytest.mark.parametrize("factory", [storage, app, sql, kv, aks])
@pytest.mark.parametrize("bad_sku", [None, {}, "Premium", {"tier": "Premium"}, {"name": "Premium"}])
def test_missing_or_malformed_sku_never_infers_a_tier(factory: Any, bad_sku: Any) -> None:
    resource = factory()
    resource["sku"] = bad_sku
    assert_no_rate(resource, "unresolved")


@pytest.mark.parametrize(
    "resource",
    [
        messaging("EventHub"),
        messaging("ServiceBus", location="westus"),
    ],
)
@pytest.mark.parametrize("key", ["name", "tier"])
def test_messaging_requires_both_sku_fields(resource: dict[str, Any], key: str) -> None:
    resource = deepcopy(resource)
    resource["sku"].pop(key)
    assert_no_rate(resource, "unresolved")


@pytest.mark.parametrize(
    "factory,key",
    [
        (app, "zoneRedundant"),
        (sql, "zoneRedundant"),
        (vm, "priority"),
        (vm, "availabilitySet"),
        (cosmos, "capabilities"),
        (cosmos, "enableMultipleWriteLocations"),
        (cosmos, "consistencyPolicy"),
        (cosmos, "locations"),
        (cosmos, "capacityMode"),
    ],
)
def test_missing_deployment_eligibility_fields_are_unresolved(factory: Any, key: str) -> None:
    resource = factory()
    resource["properties"].pop(key)
    value = assert_no_rate(resource, "unresolved")
    assert key not in value.configuration


@pytest.mark.parametrize("factory", [app, sql])
@pytest.mark.parametrize("bad_flag", [None, "false", "true", 0, 1, [], {}])
def test_deployment_flags_require_actual_booleans(factory: Any, bad_flag: Any) -> None:
    resource = factory()
    resource["properties"]["zoneRedundant"] = bad_flag
    assert_no_rate(resource, "unresolved", "boolean")


def test_storage_without_operation_does_not_use_account_default_as_blob_tier() -> None:
    resource = storage()
    resource.pop("operation")
    resource["properties"]["accessTier"] = "Hot"
    value = assert_no_rate(resource, "unresolved", "default")
    assert value.configuration["account_default_access_tier"] == "hot"
    assert "operation_selector" not in value.configuration


@pytest.mark.parametrize(
    "sku",
    [
        "Standard_LRS",
        "Standard_ZRS",
        "Standard_GRS",
        "Standard_GZRS",
        "Standard_RAGRS",
        "Standard_RAGZRS",
    ],
)
@pytest.mark.parametrize(
    "selector,rate",
    [
        ("blob_hot_write", "0.999"),
        ("blob_cool_write", "0.99"),
        ("blob_cold_write", "0.99"),
        ("blob_archive_write", "0.99"),
    ],
)
def test_storage_write_redundancies_and_tier_specific_scope(
    sku: str,
    selector: str,
    rate: str,
) -> None:
    value = resolve_service(storage(selector, sku))
    assert value.availability == rate
    assert value.configuration["operation_selector"] == selector
    assert "does not cover other tiers" in value.assumptions[0]


@pytest.mark.parametrize("sku", ["Standard_RAGRS", "Standard_RAGZRS"])
@pytest.mark.parametrize("operation", ["blob_hot_read", "blob_cool_read", "blob_cold_read"])
def test_ra_storage_read_never_promises_unverified_client_failover(
    sku: str,
    operation: str,
) -> None:
    assert_no_rate(storage(operation, sku), "unresolved", "retry")


@pytest.mark.parametrize(
    "operation",
    [
        "read",
        "write",
        "blob_archive_read",
        "file_hot_read",
        "queue_write",
        "table_read",
    ],
)
def test_unverified_storage_operations_are_not_covered(operation: str) -> None:
    assert_no_rate(storage(operation), "unsupported")


@pytest.mark.parametrize(
    "kind", ["BlobStorage", "BlockBlobStorage", "FileStorage", "Storage", "Future"]
)
def test_storage_account_kinds_are_not_silently_generalized(kind: str) -> None:
    resource = storage()
    resource["kind"] = kind
    assert_no_rate(resource, "unsupported")


def test_storage_missing_kind_and_wrong_sku() -> None:
    resource = storage()
    resource.pop("kind")
    assert_no_rate(resource, "unresolved", "kind")
    assert_no_rate(storage(sku="Standard_UNKNOWN"), "unsupported")
    assert_no_rate(storage(sku="Premium_LRS"), "unsupported")


@pytest.mark.parametrize(
    "tier,name",
    [
        ("Basic", "B1"),
        ("Standard", "S1"),
        ("Premium", "P1"),
        ("PremiumV2", "P1v2"),
        ("PremiumV3", "P1v3"),
        ("PremiumV4", "P1v4"),
    ],
)
def test_app_service_supported_regional_tiers(tier: str, name: str) -> None:
    resource = app()
    resource["sku"] = {"name": name, "tier": tier}
    assert resolve_service(resource).availability == "0.9995"


@pytest.mark.parametrize("tier,name", [("Free", "F1"), ("Shared", "D1")])
def test_app_service_no_sla_tiers(tier: str, name: str) -> None:
    resource = app()
    resource["sku"] = {"name": name, "tier": tier}
    assert_no_rate(resource, "ineligible", "no SLA")


@pytest.mark.parametrize(
    "tier,name",
    [
        ("Isolated", "I1"),
        ("IsolatedV2", "I1v2"),
        ("Basic", "P1v3"),
        ("PremiumV3", "B1"),
        ("PremiumV3", "P4v3"),
        ("PremiumV4", "P0mv4"),
        ("Future", "P1v9"),
    ],
)
def test_app_service_unsupported_or_contradictory_sku(tier: str, name: str) -> None:
    resource = app()
    resource["sku"] = {"name": name, "tier": tier}
    assert_no_rate(resource, "unsupported")


@pytest.mark.parametrize(
    "capacity,status",
    [
        (None, "unresolved"),
        ("2", "unresolved"),
        (True, "unresolved"),
        (0, "ineligible"),
        (1, "ineligible"),
    ],
)
def test_app_service_zone_capacity_must_prove_two_instances(capacity: Any, status: str) -> None:
    resource = app(True)
    resource["sku"]["capacity"] = capacity
    assert_no_rate(resource, status)


def test_app_rows_ase_and_inconsistent_instance_counts_are_not_resolved() -> None:
    assert_no_rate(row("Microsoft.Web/sites"), "unresolved", "plan")
    assert_no_rate(row("Microsoft.Web/sites", kind="functionapp,linux"), "unsupported")
    assert_no_rate(row("Microsoft.Web/sites", kind="workflowapp"), "unsupported")
    resource = app()
    resource["properties"]["hostingEnvironmentProfile"] = {"id": "ase"}
    assert_no_rate(resource, "unsupported", "Environment")
    resource = app(True)
    resource["properties"]["numberOfWorkers"] = 1
    assert_no_rate(resource, "unresolved", "Conflicting")
    resource = app(True)
    resource["sku"] = {"name": "B1", "tier": "Basic", "capacity": 2}
    assert_no_rate(resource, "unsupported", "Zone")


@pytest.mark.parametrize(
    "os_type,data_types,rate",
    [
        ("Premium_ZRS", (), "0.999"),
        ("Premium_LRS", ("PremiumV2_LRS", "UltraSSD_LRS"), "0.999"),
        ("Premium_LRS", ("StandardSSD_ZRS",), "0.995"),
        ("StandardSSD_LRS", ("Premium_LRS",), "0.995"),
        ("Premium_LRS", ("StandardSSD_LRS", "Standard_LRS"), "0.95"),
        ("Standard_LRS", ("Premium_LRS",), "0.95"),
    ],
)
def test_vm_uses_lowest_disk_commitment_not_vm_size_or_zone_count(
    os_type: str,
    data_types: tuple[str, ...],
    rate: str,
) -> None:
    resource = vm(os_type, data_types)
    resource["sku"] = {"name": "Standard_D4s_v5"}
    resource["zones"] = ["1", "2", "3"]
    assert resolve_service(resource).availability == rate


@pytest.mark.parametrize("field", ["osDisk", "dataDisks"])
def test_vm_missing_disk_inventory(field: str) -> None:
    resource = vm()
    resource["properties"]["storageProfile"].pop(field)
    assert_no_rate(resource, "unresolved", "disk")


def test_vm_referenced_disk_id_is_not_disk_tier_or_sharedness() -> None:
    resource = vm()
    resource["properties"]["storageProfile"]["osDisk"]["managedDisk"] = {"id": "/disks/os"}
    assert_no_rate(resource, "unresolved", "IDs")
    resource = vm(data_types=("Premium_LRS",))
    managed = resource["properties"]["storageProfile"]["dataDisks"][0]["managedDisk"]
    managed.pop("maxShares")
    assert_no_rate(resource, "unresolved", "sharedness")
    managed["maxShares"] = 2
    assert_no_rate(resource, "unsupported", "Shared")
    managed["maxShares"] = True
    assert_no_rate(resource, "unresolved", "sharedness")


def test_vm_native_arm_sharedness_facts_and_conflicts() -> None:
    resource = vm(data_types=("Premium_LRS",))
    managed = resource["properties"]["storageProfile"]["dataDisks"][0]["managedDisk"]
    managed.pop("maxShares")
    managed["additionalDiskProperties"] = {"managedDiskProperties": {"maxShares": 1}}
    assert resolve_service(resource).availability == "0.999"
    managed["maxShares"] = 2
    assert_no_rate(resource, "unresolved", "Conflicting")


@pytest.mark.parametrize("key", ["availabilitySet", "virtualMachineScaleSet", "host", "hostGroup"])
def test_vm_no_multi_resource_or_dedicated_host_uplift(key: str) -> None:
    resource = vm()
    resource["properties"][key] = {"id": "group"}
    assert_no_rate(resource, "unsupported", "group")


@pytest.mark.parametrize("priority", ["Spot", "Low"])
def test_spot_vm_is_ineligible(priority: str) -> None:
    resource = vm()
    resource["properties"]["priority"] = priority
    assert_no_rate(resource, "ineligible", "no SLA")


@pytest.mark.parametrize(
    "os_type", ["PremiumV2_LRS", "UltraSSD_LRS", "FutureDisk", "StandardSSD_UNKNOWN"]
)
def test_unverified_os_disk_types(os_type: str) -> None:
    assert_no_rate(vm(os_type), "unsupported")


def test_ephemeral_vm_is_not_managed_disk_sla() -> None:
    resource = vm()
    resource["properties"]["storageProfile"]["osDisk"]["diffDiskSettings"] = {"option": "Local"}
    assert_no_rate(resource, "unsupported", "Ephemeral")
    resource["properties"]["storageProfile"]["osDisk"]["diffDiskSettings"] = "Local"
    assert_no_rate(resource, "unresolved", "Malformed")


def test_unmanaged_vhd_cannot_be_classified_as_managed_vm_disk() -> None:
    resource = vm()
    resource["properties"]["storageProfile"]["osDisk"]["vhd"] = {"uri": "not-output"}
    assert_no_rate(resource, "unsupported", "Unmanaged")
    resource = vm(data_types=("Premium_LRS",))
    resource["properties"]["storageProfile"]["dataDisks"][0]["vhd"] = {"uri": "not-output"}
    assert_no_rate(resource, "unsupported", "Unmanaged")


@pytest.mark.parametrize(
    "tier,name",
    [
        ("Basic", "Basic"),
        ("Standard", "S0"),
        ("Premium", "P1"),
        ("GeneralPurpose", "GP_Gen5"),
        ("BusinessCritical", "BC_Gen5"),
        ("Hyperscale", "HS_Gen5"),
    ],
)
def test_sql_verified_tiers(tier: str, name: str) -> None:
    resource = sql()
    resource["sku"] = {"name": name, "tier": tier}
    assert resolve_service(resource).availability == "0.9999"
    resource["properties"]["zoneRedundant"] = True
    if tier in {"Basic", "Standard"}:
        assert_no_rate(resource, "unsupported")
    else:
        assert resolve_service(resource).availability == "0.99995"


@pytest.mark.parametrize(
    "tier,name",
    [
        ("GeneralPurpose", "BC_Gen5"),
        ("GeneralPurpose", "GP_Unknown"),
        ("GeneralPurpose", "GP_S_Gen5"),
        ("Hyperscale", "HS_S_Gen5"),
        ("ElasticPool", "ElasticPool"),
    ],
)
def test_sql_unknown_inconsistent_pooled_or_serverless_skus(tier: str, name: str) -> None:
    resource = sql()
    resource["sku"] = {"name": name, "tier": tier}
    assert_no_rate(resource, "unsupported")


def test_sql_pooled_auto_pause_and_master_require_other_eligibility() -> None:
    resource = sql()
    resource["properties"]["elasticPoolId"] = "/pool/id"
    assert_no_rate(resource, "unresolved", "pool")
    resource = sql()
    resource["properties"]["autoPauseDelay"] = 60
    assert_no_rate(resource, "unsupported", "pausing")
    resource = sql()
    resource["name"] = "server/master"
    assert_no_rate(resource, "unsupported", "master")


@pytest.mark.parametrize(
    "consistency",
    [
        "Strong",
        "BoundedStaleness",
        "Session",
        "ConsistentPrefix",
        "Eventual",
    ],
)
def test_cosmos_all_five_consistency_levels(consistency: str) -> None:
    resource = cosmos()
    resource["properties"]["consistencyPolicy"]["defaultConsistencyLevel"] = consistency
    assert resolve_service(resource).availability == "0.9999"


@pytest.mark.parametrize(
    "capabilities",
    [
        [{"name": "EnableServerless"}],
        [{"name": "FutureCapability"}],
    ],
)
def test_cosmos_serverless_or_unknown_capabilities(capabilities: list[dict[str, Any]]) -> None:
    resource = cosmos()
    resource["properties"]["capabilities"] = capabilities
    assert_no_rate(resource, "unsupported")


@pytest.mark.parametrize("value", [None, "false", 0])
def test_cosmos_write_flag_missing_or_wrong_type(value: Any) -> None:
    resource = cosmos()
    resource["properties"]["enableMultipleWriteLocations"] = value
    assert_no_rate(resource, "unresolved")


def test_cosmos_multiple_regions_and_writes_have_no_speculative_uplift() -> None:
    resource = cosmos()
    resource["properties"]["locations"].append(
        {"locationName": "westus", "isZoneRedundant": True},
    )
    assert_no_rate(resource, "unsupported", "Multi-region")
    resource = cosmos()
    resource["properties"]["enableMultipleWriteLocations"] = True
    assert_no_rate(resource, "unsupported", "Multiple-write")


@pytest.mark.parametrize("field", ["locationName", "isZoneRedundant"])
def test_cosmos_missing_region_eligibility(field: str) -> None:
    resource = cosmos()
    resource["properties"]["locations"][0].pop(field)
    assert_no_rate(resource, "unresolved")


@pytest.mark.parametrize(
    "capability", ["EnableMongo", "EnableCassandra", "EnableGremlin", "EnableTable"]
)
def test_cosmos_ru_apis_use_same_single_region_clause(capability: str) -> None:
    resource = cosmos()
    resource["properties"]["capabilities"] = [{"name": capability}]
    if capability == "EnableMongo":
        resource["kind"] = "MongoDB"
    assert resolve_service(resource).availability == "0.9999"


def test_cosmos_missing_kind_unknown_api_and_inconsistent_mongo() -> None:
    resource = cosmos()
    resource.pop("kind")
    assert_no_rate(resource, "unresolved", "kind")
    resource["kind"] = "PostgreSQL"
    assert_no_rate(resource, "unsupported")
    resource["kind"] = "MongoDB"
    assert_no_rate(resource, "unresolved", "inconsistent")


@pytest.mark.parametrize(
    "mode,status",
    [
        (None, "unresolved"),
        ("None", "unresolved"),
        ("Future", "unresolved"),
        ("Serverless", "unsupported"),
    ],
)
def test_cosmos_capacity_mode_never_inferred_from_empty_capabilities(
    mode: Any,
    status: str,
) -> None:
    resource = cosmos()
    resource["properties"]["capacityMode"] = mode
    assert_no_rate(resource, status, "capacityMode")


def test_cosmos_actual_region_name_and_az_support_are_verified() -> None:
    resource = cosmos(True)
    resource["properties"]["locations"][0]["locationName"] = "East US"
    assert resolve_service(resource).availability == "0.99995"
    resource["properties"]["locations"][0]["locationName"] = "West US"
    assert_no_rate(resource, "unsupported", "conflicts")
    resource["properties"]["locations"][0]["locationName"] = "future-region"
    assert_no_rate(resource, "unresolved", "pinned")


def test_cosmos_conflicting_api_configuration_is_not_eligible() -> None:
    resource = cosmos()
    resource["properties"]["capabilities"] = [{"name": "EnableMongo"}]
    assert_no_rate(resource, "unresolved", "conflicts")
    resource["properties"]["capabilities"].append({"name": "EnableCassandra"})
    assert_no_rate(resource, "unsupported", "Conflicting")


@pytest.mark.parametrize("tier", ["standard", "premium"])
def test_key_vault_actual_arm_sku_has_family_not_tier(tier: str) -> None:
    value = resolve_service(kv(tier))
    assert value.availability == "0.9999"
    assert "creating, updating or deleting" in value.operation


def test_key_vault_incorrect_sku_family_or_missing_family() -> None:
    resource = kv()
    resource["sku"]["family"] = "B"
    assert_no_rate(resource, "unsupported")
    resource["sku"].pop("family")
    assert_no_rate(resource, "unresolved")
    assert_no_rate(kv("managedhsm"), "unsupported")
    assert_no_rate(row("Microsoft.KeyVault/managedHSMs"), "unsupported")


def test_key_vault_raw_arg_nested_sku_and_conflicting_metadata() -> None:
    resource = row(
        "Microsoft.KeyVault/vaults",
        properties={"sku": {"name": "premium", "family": "A"}},
        sku=None,
    )
    value = resolve_service(resource)
    assert value.availability == "0.9999"
    assert value.sku == "premium"
    resource["sku"] = {"name": "Premium"}
    assert resolve_service(resource).availability == "0.9999"
    resource["sku"] = {"name": "standard", "family": "A"}
    assert_no_rate(resource, "unresolved", "Conflicting")
    resource.pop("sku")
    resource["properties"]["sku"].pop("family")
    assert_no_rate(resource, "unresolved")


@pytest.mark.parametrize("tier", ["Basic", "Standard", "Premium"])
def test_service_bus_all_tiers_outside_az_regions(tier: str) -> None:
    assert (
        resolve_service(messaging("ServiceBus", tier, location="canadaeast")).availability
        == "0.999"
    )


@pytest.mark.parametrize("tier", ["Basic", "Standard"])
def test_service_bus_legal_table_gap_for_basic_standard_in_az_regions(tier: str) -> None:
    assert_no_rate(messaging("ServiceBus", tier), "unresolved", "extrapolated")


@pytest.mark.parametrize(
    "count,status",
    [
        (None, "unresolved"),
        ("2", "unresolved"),
        (True, "unresolved"),
        (0, "unsupported"),
        (1, "unsupported"),
        (3, "unsupported"),
    ],
)
def test_service_bus_requires_known_supported_partition_count(count: Any, status: str) -> None:
    resource = messaging("ServiceBus", "Premium", properties={"premiumMessagingPartitions": count})
    assert_no_rate(resource, status)


@pytest.mark.parametrize("deprecated_flag", [False, True, None])
@pytest.mark.parametrize("partitions", [2, 4])
def test_service_bus_deprecated_zone_flag_cannot_override_region_fact(
    deprecated_flag: Any,
    partitions: int,
) -> None:
    resource = messaging(
        "ServiceBus",
        "Premium",
        properties={
            "premiumMessagingPartitions": partitions,
            "zoneRedundant": deprecated_flag,
        },
    )
    value = resolve_service(resource)
    assert value.availability == "0.9999"
    assert "zone_redundant" not in value.configuration


@pytest.mark.parametrize(
    "tier,rate", [("Basic", "0.9995"), ("Standard", "0.9995"), ("Premium", "0.9999")]
)
def test_event_hubs_sku_tiers(tier: str, rate: str) -> None:
    assert resolve_service(messaging("EventHub", tier)).availability == rate


def test_messaging_inconsistent_or_dedicated_namespace_sku() -> None:
    for family in ("EventHub", "ServiceBus"):
        resource = messaging(family)
        resource["sku"]["name"] = "Premium"
        assert_no_rate(resource, "unsupported", "inconsistent")
    assert_no_rate(messaging("EventHub", "Dedicated"), "unsupported")
    resource = messaging("EventHub", properties={"clusterArmId": "/cluster/id"})
    assert_no_rate(resource, "unsupported", "Dedicated")


@pytest.mark.parametrize("tier", ["Standard", "Premium"])
@pytest.mark.parametrize("node_zones", [None, [], ["1"], ["1", "2", "3"]])
def test_aks_control_plane_az_is_not_inferred_from_node_pool_zones(
    tier: str,
    node_zones: Any,
) -> None:
    resource = aks(tier)
    resource["properties"]["agentPoolProfiles"] = [{"availabilityZones": node_zones}]
    value = resolve_service(resource)
    assert value.availability == "0.9995"
    assert value.operation == "Kubernetes API-server connectivity only"
    resource["location"] = "westus"
    assert resolve_service(resource).availability == "0.999"


def test_aks_free_automatic_and_legacy_paid_are_not_supported_rates() -> None:
    assert_no_rate(aks("Free"), "ineligible")
    assert_no_rate(aks("Paid"), "unsupported")
    resource = aks()
    resource["sku"]["name"] = "Automatic"
    assert_no_rate(resource, "unsupported", "Automatic")


@pytest.mark.parametrize(
    "resource",
    [
        aks(location="future-region"),
        messaging("ServiceBus", "Premium", location="future-region"),
    ],
)
def test_unknown_region_does_not_mean_non_zonal(resource: dict[str, Any]) -> None:
    assert_no_rate(resource, "unresolved", "pinned")


def test_load_balancer_count_and_zones_cannot_prove_healthy_backend_group() -> None:
    resource = row(
        "Microsoft.Network/loadBalancers",
        sku={"name": "Standard"},
        zones=["1", "2", "3"],
        properties={
            "backendAddressPools": [
                {
                    "properties": {
                        "loadBalancerBackendAddresses": [
                            {"name": "vm1"},
                            {"name": "vm2"},
                        ]
                    }
                }
            ]
        },
    )
    assert_no_rate(resource, "unresolved", "healthy")
    resource["sku"]["name"] = "Basic"
    assert_no_rate(resource, "unsupported", "Basic")


@pytest.mark.parametrize(
    "resource_type",
    [
        "Microsoft.Compute/virtualMachineScaleSets",
        "Microsoft.Sql/managedInstances",
        "Microsoft.DocumentDB/mongoClusters",
        "Microsoft.Storage/storageAccounts/blobServices",
        "Microsoft.ServiceBus/namespaces/queues",
        "Microsoft.EventHub/namespaces/eventhubs",
        "Microsoft.Web/hostingEnvironments",
        "Microsoft.Future/services",
    ],
)
def test_other_resource_types_do_not_inherit_parent_family_rate(resource_type: str) -> None:
    assert_no_rate(row(resource_type), "unsupported")


@pytest.mark.parametrize("state", ["Creating", "Failed", "Deleting", "Future"])
def test_pending_or_failed_deployment_is_unresolved(state: str) -> None:
    resource = kv()
    resource["properties"]["provisioningState"] = state
    assert_no_rate(resource, "unresolved", "Succeeded")


@pytest.mark.parametrize("props", [None, "raw", [], 1])
def test_malformed_properties_fail_closed(props: Any) -> None:
    resource = kv()
    resource["properties"] = props
    assert_no_rate(resource, "unresolved", "Malformed")


def test_no_raw_properties_secrets_mutation_or_live_web_lookups() -> None:
    resource = vm(data_types=("Premium_LRS",))
    resource["properties"].update(
        {
            "adminPassword": "do-not-output",
            "connectionString": "do-not-output",
            "storageProfileSecret": {"secret": "do-not-output"},
            "provisioningState": "Succeeded",
        }
    )
    resource["properties"]["storageProfile"]["osDisk"]["managedDisk"]["credential"] = (
        "do-not-output"
    )
    resource["tags"] = {"secret": "do-not-output"}
    resource["zones"] = ["do-not-output"]
    before = deepcopy(resource)
    with patch("socket.socket.connect", side_effect=AssertionError("no network")):
        value = resolve_service(resource)
    assert value.status == "resolved"
    assert resource == before
    assert "do-not-output" not in value.model_dump_json()
    assert "properties" not in value.configuration
    assert value.configuration["deployment_state"] == "succeeded"


def test_identity_normalization_and_missing_sku_remain_distinct() -> None:
    resource = vm()
    resource["id"] = f"  {resource['id']}/  "
    resource["type"] = " MICROSOFT.COMPUTE/VIRTUALMACHINES "
    resource["location"] = " EastUS "
    value = resolve_service(resource)
    assert value.resource_id == resource["id"].strip()
    assert value.component_id == resource["id"].strip().rstrip("/").lower()
    assert value.resource_type == "microsoft.compute/virtualmachines"
    assert value.location == "eastus"
    assert value.sku == ""
    assert "sku_tier" not in value.configuration


@pytest.mark.parametrize("availability", ["-0.1", "1.001", "99.95", "NaN", "Infinity", "1e-3", ""])
def test_model_rejects_non_decimal_or_out_of_range_availability(availability: str) -> None:
    value = resolve_service(kv()).model_dump()
    value["availability"] = availability
    with pytest.raises(ValidationError):
        ServiceCommitment.model_validate(value)


@pytest.mark.parametrize("availability", ["0", "0.9995", "1", "1.0"])
def test_model_accepts_decimal_bounds(availability: str) -> None:
    value = resolve_service(kv()).model_dump()
    value["availability"] = availability
    assert ServiceCommitment.model_validate(value).availability == availability


def test_model_enforces_status_and_evidence_and_allowlisted_config() -> None:
    value = resolve_service(kv()).model_dump()
    for change in (
        {"status": "unresolved"},
        {"availability": None},
        {"source_url": ""},
        {"source_version": ""},
        {"rule_id": ""},
        {"operation": ""},
        {"configuration": {"properties": {"credential": "not-allowed"}}},
        {"configuration": {"sku_tier": {"credential": "not-allowed"}}},
        {"configuration": {"account_regions": [{"credential": "not-allowed"}]}},
    ):
        with pytest.raises(ValidationError):
            ServiceCommitment.model_validate({**value, **change})
    first = ServiceCommitment(
        component_id="",
        resource_id="",
        name="",
        resource_type="",
        location="",
        sku="",
        status="unresolved",
    )
    second = ServiceCommitment(
        component_id="",
        resource_id="",
        name="",
        resource_type="",
        location="",
        sku="",
        status="unresolved",
    )
    first.assumptions.append("one")
    first.configuration["sku_tier"] = "standard"
    assert second.assumptions == []
    assert second.configuration == {}
