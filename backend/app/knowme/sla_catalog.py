"""Offline, configuration-scoped Microsoft service commitments, not observed uptime.

Primary source, read directly on 2026-10-09:
Microsoft Online Services SLA, October 1, 2026 (English), the version advertised at
https://www.microsoft.com/licensing/docs/view/Service-Level-Agreements-SLA-for-Online-Services
The version-specific download URL is SLA_URL below. Rule clauses quote the actual
section/table labels and first service-credit threshold, not marketing availability.
Retrieved document SHA-256:
77e3e636513b0b3be8ada4d976d91d4543dd12564c418f2f0ead8fa5b7166f41

Eligibility cross-checks (all retrieved 2026-10-09):
https://learn.microsoft.com/en-us/azure/reliability/reliability-storage-blob
https://learn.microsoft.com/en-us/azure/reliability/reliability-app-service
https://learn.microsoft.com/en-us/azure/reliability/reliability-virtual-machines
https://learn.microsoft.com/en-us/azure/reliability/reliability-sql-database
https://learn.microsoft.com/en-us/azure/reliability/reliability-cosmos-db
https://learn.microsoft.com/en-us/azure/reliability/reliability-key-vault
https://learn.microsoft.com/en-us/azure/reliability/reliability-service-bus
https://learn.microsoft.com/en-us/azure/reliability/reliability-event-hubs
https://learn.microsoft.com/en-us/azure/reliability/reliability-aks
https://learn.microsoft.com/en-us/azure/reliability/reliability-load-balancer
https://learn.microsoft.com/en-us/azure/app-service/app-service-configure-premium-v3-tier
https://azure.microsoft.com/en-us/pricing/details/app-service/windows/

Important boundaries:
* Storage requires an OPTIONAL operation selector, e.g. resource["operation"] =
  "blob_hot_write". This scopes the requested commitment to that operation AND tier.
  An account's default accessTier does NOT prove any individual blob's tier. Without
  a selector the account is unresolved, never assigned a blanket percentage.
* App Service plans expose the entitlement for eligible hosted apps, not a standalone
  compute-plan uptime promise. App rows need a plan join and remain unresolved here.
* VM disks must carry their actual storageAccountType; disk IDs are not disk types.
  Nonempty dataDisks additionally need managedDisk.maxShares = 1, an allowlisted fact
  from the referenced disk, or the native additionalDiskProperties.managedDiskProperties
  .maxShares field. Missing disk facts or availability-set membership cannot be guessed.
  Availability sets, VMSS, and dedicated/shared-disk groups aren't covered.
* Cosmos DB covers provisioned-throughput accounts in exactly one region, not
  serverless, PostgreSQL, MongoDB vCore, or multi-region/group commitments.
  A modern account must explicitly establish properties.capacityMode = Provisioned;
  absence of EnableServerless alone is not treated as proof of capacity mode.
* AKS covers Kubernetes API-server connectivity, never node/pod/application uptime.
  The official AKS pricing-tier doc states that control planes are automatically
  zone-distributed in AZ regions, regardless of node-pool zone configuration:
  https://learn.microsoft.com/en-us/azure/aks/free-standard-pricing-tiers
* The bounded region map is pinned to the official region table, not inferred from
  a resource's zones array:
  https://learn.microsoft.com/en-us/azure/reliability/regions-list
* Service Bus's deprecated zoneRedundant flag isn't an eligibility signal. Reliability
  guidance additionally requires partitioning for the Premium AZ uplift:
  https://learn.microsoft.com/en-us/azure/service-bus-messaging/enable-partitions-premium
  ("premiumMessagingPartitions ... larger than 1"). The legal table only states the
  all-tier base rate for regions WITHOUT AZ support; Basic/Standard in AZ regions
  therefore remain unresolved rather than extrapolating a rate.
* Load Balancer requires at least two healthy backend VMs; an ARG row cannot prove
  their health. No numerical Load Balancer or multi-resource redundancy rule exists.
* ARM eligibility field locations/values were cross-checked against:
  https://learn.microsoft.com/en-us/azure/templates/microsoft.keyvault/vaults
  (the vault SKU is properties.sku, with name Standard/Premium and family A),
  https://learn.microsoft.com/en-us/azure/templates/microsoft.documentdb/databaseaccounts
  (capacityMode = None/Provisioned/Serverless), and
  https://learn.microsoft.com/en-us/azure/templates/microsoft.compute/virtualmachines.

General agreement exclusions, applicable-period/subscription aggregation, claim
requirements, and customer responsibilities still apply. A resolved result is a
conditional service commitment, not a credit determination or workload SLA.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

CATALOG_VERSION = "2026-10-09.1"
SLA_URL = (
    "https://www.microsoft.com/licensing/docs/documents/download/"
    "OnlineSvcsConsolidatedSLA(WW)(English)(October_2026)(CR).docx"
)
SOURCE_VERSION = "Microsoft Online Services SLA, October 1, 2026; retrieved 2026-10-09"

_CONFIG_KEYS = frozenset(
    {
        "sku_name",
        "sku_tier",
        "account_kind",
        "replication",
        "operation_selector",
        "account_default_access_tier",
        "zone_redundant",
        "instance_count",
        "priority",
        "availability_set",
        "os_disk_type",
        "data_disk_types",
        "data_disk_max_shares",
        "capabilities",
        "capacity_mode",
        "consistency",
        "account_regions",
        "multiple_write_locations",
        "region_availability_zones",
        "premium_messaging_partitions",
        "cluster_kind",
        "deployment_state",
    }
)


class ServiceCommitment(BaseModel):
    component_id: str
    resource_id: str
    name: str
    resource_type: str
    location: str
    sku: str
    configuration: dict[str, Any] = Field(default_factory=dict)
    status: Literal["resolved", "unresolved", "unsupported", "ineligible"]
    availability: str | None = None
    rule_id: str = ""
    operation: str = ""
    source_url: str = ""
    source_version: str = ""
    measurement_basis: str = "monthly availability"
    reason: str = ""
    assumptions: list[str] = Field(default_factory=list)

    @field_validator("availability")
    @classmethod
    def _decimal_availability(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"(?:0(?:\.\d+)?|1(?:\.0+)?)", value):
            raise ValueError("availability must be a decimal string in [0, 1]")
        return value

    @field_validator("configuration")
    @classmethod
    def _allowlisted_configuration(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.keys() - _CONFIG_KEYS:
            raise ValueError("configuration contains non-allowlisted eligibility facts")
        list_keys = {
            "data_disk_types": str,
            "data_disk_max_shares": int,
            "capabilities": str,
            "account_regions": str,
        }
        bool_keys = {"zone_redundant", "multiple_write_locations", "region_availability_zones"}
        int_keys = {"instance_count", "premium_messaging_partitions"}
        for key, fact in value.items():
            if key in list_keys:
                if not isinstance(fact, list) or any(type(v) is not list_keys[key] for v in fact):
                    raise ValueError("configuration list facts must contain only typed scalars")
            elif key in bool_keys:
                if type(fact) is not bool:
                    raise ValueError("configuration flags must be booleans")
            elif key in int_keys:
                if type(fact) is not int:
                    raise ValueError("configuration counts must be integers")
            elif key == "availability_set":
                if fact is not None:
                    raise ValueError(
                        "only explicit absence of availability-set membership is allowed"
                    )
            elif not isinstance(fact, str):
                raise ValueError("configuration text facts must be strings, never raw objects")
        return value

    @model_validator(mode="after")
    def _status_and_evidence(self) -> ServiceCommitment:
        if self.status == "resolved":
            if self.availability is None or not all(
                (
                    self.rule_id,
                    self.operation,
                    self.source_url,
                    self.source_version,
                )
            ):
                raise ValueError("resolved commitments require availability and primary evidence")
        elif self.availability is not None:
            raise ValueError("non-resolved commitments cannot carry numerical availability")
        return self


@dataclass(frozen=True)
class _Rule:
    availability: str
    operation: str
    clause: str
    basis: str = "monthly availability"


_RULES = {
    "storage.blob.hot.write": _Rule(
        "0.999",
        "Hot-tier blob writes",
        "Storage Accounts / Hot and Transaction Optimized Access Tiers / "
        "LRS, ZRS, GRS, GZRS, RA-GRS, RA-GZRS write requests: < 99.9%.",
        "100% minus average hourly storage transaction error rate in the applicable month",
    ),
    "storage.blob.hot.read": _Rule(
        "0.999",
        "Hot-tier blob reads",
        "Storage Accounts / Hot and Transaction Optimized Access Tiers / "
        "LRS, ZRS, GRS, GZRS read requests: < 99.9%.",
        "100% minus average hourly storage transaction error rate in the applicable month",
    ),
    "storage.blob.cool.write": _Rule(
        "0.99",
        "Cool-tier blob writes",
        "Storage Accounts / Cool, Cold, and Archive Tiers / write requests: < 99%.",
        "100% minus average hourly storage transaction error rate in the applicable month",
    ),
    "storage.blob.cool.read": _Rule(
        "0.999",
        "Cool-tier blob reads",
        "Storage Accounts / Cool, Cold, and Archive Tiers / "
        "LRS, ZRS, GRS, GZRS read requests: < 99.9%.",
        "100% minus average hourly storage transaction error rate in the applicable month",
    ),
    "storage.blob.cold.write": _Rule(
        "0.99",
        "Cold-tier blob writes",
        "Storage Accounts / Cool, Cold, and Archive Tiers / write requests: < 99%.",
        "100% minus average hourly storage transaction error rate in the applicable month",
    ),
    "storage.blob.cold.read": _Rule(
        "0.999",
        "Cold-tier blob reads",
        "Storage Accounts / Cool, Cold, and Archive Tiers / "
        "LRS, ZRS, GRS, GZRS read requests: < 99.9%.",
        "100% minus average hourly storage transaction error rate in the applicable month",
    ),
    "storage.blob.archive.write": _Rule(
        "0.99",
        "Archive-tier blob writes",
        "Storage Accounts / Cool, Cold, and Archive Tiers / write requests: < 99%.",
        "100% minus average hourly storage transaction error rate in the applicable month",
    ),
    "app-service.regional": _Rule(
        "0.9995",
        "Eligible hosted app connectivity (App Service plan entitlement)",
        "App Service / Apps that don't use Availability Zones: < 99.95%; "
        "'The SLA is supported when running on a single instance and on multiple instances.'",
    ),
    "app-service.zone-redundant": _Rule(
        "0.9999",
        "Eligible hosted app connectivity (zone-redundant plan entitlement)",
        "App Service / Apps deployed across two or more Availability Zones "
        "in the same region: < 99.99%.",
    ),
    "vm.single.premium": _Rule(
        "0.999",
        "Single-instance VM connectivity",
        "Virtual Machines / Single-Instance Virtual Machines by Disk type: "
        "Premium SSD, Premium SSD v2, Ultra Disk < 99.9%; Premium SSD for all OS disks "
        "and Premium SSD, Premium SSD v2 or Ultra Disk for all data disks.",
    ),
    "vm.single.standard-ssd": _Rule(
        "0.995",
        "Single-instance VM connectivity",
        "Virtual Machines / Single-Instance Virtual Machines by Disk type: "
        "Standard SSD Managed Disk < 99.5%; lowest SLA of all attached disk types applies.",
    ),
    "vm.single.standard-hdd": _Rule(
        "0.95",
        "Single-instance VM connectivity",
        "Virtual Machines / Single-Instance Virtual Machines by Disk type: "
        "Standard HDD Managed Disk < 95%; lowest SLA of all attached disk types applies.",
    ),
    "sql.regional": _Rule(
        "0.9999",
        "SQL Database connection availability",
        "Azure SQL Database / Basic or Standard, and non-zone-redundant "
        "Hyperscale, Business Critical, Premium or General Purpose tables: < 99.99%.",
    ),
    "sql.zone-redundant": _Rule(
        "0.99995",
        "SQL Database connection availability",
        "Azure SQL Database / General Purpose, Business Critical, Premium or "
        "Hyperscale configured for Zone Redundant Deployments: < 99.995%.",
    ),
    "cosmos.single-region": _Rule(
        "0.9999",
        "Provisioned-throughput Cosmos DB reads and writes, single region",
        "Azure Cosmos DB / Availability Percentage single region / "
        "Service Credit for Provisioned Throughput Resources: < 99.99%.",
        "100% minus average hourly request error rate in the applicable month",
    ),
    "cosmos.single-region-zonal": _Rule(
        "0.99995",
        "Provisioned-throughput Cosmos DB reads and writes, single region with AZ",
        "Azure Cosmos DB / Availability Percentage single-region with availability zones "
        "(SR-AZ) / Service Credit for Provisioned Throughput Resources: < 99.995%.",
        "100% minus average hourly request error rate in the applicable month",
    ),
    "key-vault.vault": _Rule(
        "0.9999",
        "Key Vault transactions other than creating, updating or deleting vaults/keys/secrets",
        "Key Vault / Service Credit / Uptime Percentage: < 99.99%. "
        "Excluded Transactions are creating, updating or deleting key vaults, keys or secrets.",
    ),
    "service-bus.non-az-region": _Rule(
        "0.999",
        "Service Bus Queue and Topic messaging",
        "Service-Bus / Queues and Topics in all tiers deployed outside regions "
        "with Availability Zone support: < 99.9%.",
    ),
    "service-bus.premium.partitioned-az": _Rule(
        "0.9999",
        "Service Bus Premium Queue and Topic messaging",
        "Service-Bus / Queues and Topics in the Premium tier deployed in regions "
        "with Availability Zone support: < 99.99%; reliability guidance additionally "
        "requires partitioning.",
    ),
    "event-hubs.basic-standard": _Rule(
        "0.9995",
        "Event Hubs send, receive and other eligible operations",
        "Event Hubs / Basic and Standard tiers: < 99.95%.",
    ),
    "event-hubs.premium": _Rule(
        "0.9999",
        "Event Hubs send, receive and other eligible operations",
        "Event Hubs / Premium or Dedicated tiers: < 99.99%; this rule only covers Premium.",
    ),
    "aks.control-plane.zonal": _Rule(
        "0.9995",
        "Kubernetes API-server connectivity only",
        "Azure Kubernetes Service (AKS) / Clusters that have Availability Zones "
        "enabled in the region: < 99.95%; Standard/Premium tier required.",
    ),
    "aks.control-plane.regional": _Rule(
        "0.999",
        "Kubernetes API-server connectivity only",
        "Azure Kubernetes Service (AKS) / Clusters that don't have Availability Zones "
        "enabled: < 99.9%; Standard/Premium tier required.",
    ),
}

# A deliberately bounded subset of the table retrieved 2026-10-09. Unknown != nonzonal.
_AZ_REGIONS = frozenset(
    {
        "canadacentral",
        "centralus",
        "eastus",
        "eastus2",
        "francecentral",
        "germanywestcentral",
        "northcentralus",
        "northeurope",
        "southcentralus",
        "uksouth",
        "westeurope",
        "westus2",
        "westus3",
    }
)
_NON_AZ_REGIONS = frozenset(
    {
        "canadaeast",
        "francesouth",
        "germanynorth",
        "norwaywest",
        "ukwest",
        "westcentralus",
        "westus",
    }
)

_TYPES = frozenset(
    {
        "microsoft.storage/storageaccounts",
        "microsoft.web/serverfarms",
        "microsoft.web/sites",
        "microsoft.compute/virtualmachines",
        "microsoft.sql/servers/databases",
        "microsoft.documentdb/databaseaccounts",
        "microsoft.keyvault/vaults",
        "microsoft.servicebus/namespaces",
        "microsoft.eventhub/namespaces",
        "microsoft.containerservice/managedclusters",
        "microsoft.network/loadbalancers",
    }
)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _stop(
    result: ServiceCommitment,
    status: Literal["unresolved", "unsupported", "ineligible"],
    reason: str,
) -> ServiceCommitment:
    result.status = status
    result.reason = reason
    return result


def _resolved(result: ServiceCommitment, rule_id: str) -> ServiceCommitment:
    rule = _RULES[rule_id]
    result.status = "resolved"
    result.availability = rule.availability
    result.rule_id = rule_id
    result.operation = rule.operation
    result.measurement_basis = rule.basis
    result.reason = rule.clause
    result.assumptions.append(
        "Conditional Microsoft service commitment, not observed uptime or a workload SLA; "
        "agreement exclusions, subscription/period aggregation and credit-claim terms apply."
    )
    return result


def _tier(
    result: ServiceCommitment,
    raw: Any,
    allowed: set[str],
) -> str | None:
    sku = _object(raw)
    name, tier = _text(sku.get("name")).lower(), _text(sku.get("tier")).lower()
    if not name or not tier:
        _stop(result, "unresolved", "Missing sku.name or sku.tier; no tier is inferred.")
        return None
    if tier not in allowed:
        _stop(result, "unsupported", "Explicit SKU tier is outside this catalog's verified rules.")
        return None
    result.configuration["sku_tier"] = tier
    return tier


def _region_zones(result: ServiceCommitment) -> bool | None:
    if result.location in _AZ_REGIONS:
        value = True
    elif result.location in _NON_AZ_REGIONS:
        value = False
    else:
        _stop(result, "unresolved", "Region AZ support is absent from the pinned verified subset.")
        return None
    result.configuration["region_availability_zones"] = value
    return value


def _storage(result: ServiceCommitment, resource: dict[str, Any]) -> ServiceCommitment:
    props, sku = _object(resource.get("properties")), _object(resource.get("sku"))
    tier = _tier(result, sku, {"standard"})
    if tier is None:
        return result
    kind = _text(resource.get("kind")).lower()
    if not kind:
        return _stop(result, "unresolved", "Missing storage account kind.")
    if kind != "storagev2":
        return _stop(
            result, "unsupported", "Only Standard StorageV2 blob operation scopes are covered."
        )
    name = _text(sku.get("name")).lower()
    replication = name.removeprefix("standard_")
    if name != f"standard_{replication}" or replication not in {
        "lrs",
        "zrs",
        "grs",
        "gzrs",
        "ragrs",
        "ragzrs",
    }:
        return _stop(
            result, "unsupported", "Storage SKU name does not identify a verified redundancy."
        )
    result.configuration.update(sku_name=name, account_kind=kind, replication=replication)
    default_tier = _text(props.get("accessTier")).lower()
    if default_tier in {"hot", "cool", "cold", "archive"}:
        result.configuration["account_default_access_tier"] = default_tier
    selector = _text(resource.get("operation")).lower()
    if not selector:
        return _stop(
            result,
            "unresolved",
            "Storage requires an explicit blob_<tier>_<read|write> operation selector. "
            "Account accessTier is only a default, not proof of effective blob tiers.",
        )
    rule_id = selector.replace("_", ".")
    rule_id = f"storage.{rule_id}"
    if rule_id not in _RULES or not rule_id.startswith("storage."):
        return _stop(
            result,
            "unsupported",
            "Unsupported Storage operation/tier scope, including archive reads.",
        )
    result.configuration["operation_selector"] = selector
    if selector.endswith("_read") and replication in {"ragrs", "ragzrs"}:
        return _stop(
            result,
            "unresolved",
            "RA-GRS/RA-GZRS read eligibility requires verified secondary-region retry behavior "
            "and excludes geo-replication-lag failures; an ARG row does not establish this.",
        )
    result.assumptions.append(
        "The caller explicitly selected this blob operation/tier scope. The commitment "
        "does not cover other tiers, excluded transactions, Files, Queues or Tables."
    )
    return _resolved(result, rule_id)


def _app_service(result: ServiceCommitment, resource: dict[str, Any]) -> ServiceCommitment:
    if result.resource_type == "microsoft.web/sites":
        kinds = {k.strip() for k in _text(resource.get("kind")).lower().split(",")}
        if kinds & {"functionapp", "workflowapp"}:
            return _stop(
                result, "unsupported", "Functions/workflow-app eligibility is not implemented."
            )
        return _stop(
            result,
            "unresolved",
            "An app row does not prove its hosting plan tier/zone configuration; "
            "resolve the referenced App Service plan. Functions and deployment slots "
            "are not covered by the plan rules.",
        )
    sku, props = _object(resource.get("sku")), _object(resource.get("properties"))
    tier = _tier(
        result,
        sku,
        {
            "free",
            "shared",
            "basic",
            "standard",
            "premium",
            "premiumv2",
            "premiumv3",
            "premiumv4",
        },
    )
    if tier is None:
        return result
    if tier in {"free", "shared"}:
        return _stop(
            result,
            "ineligible",
            "Free/Shared plans have no SLA: "
            "https://azure.microsoft.com/en-us/pricing/details/app-service/windows/ "
            "(retrieved 2026-10-09).",
        )
    patterns = {
        "basic": r"b[123]",
        "standard": r"s[123]",
        "premium": r"p[123]",
        "premiumv2": r"p[123]v2",
        "premiumv3": r"(?:p[0-3]v3|p[1-5]mv3)",
        "premiumv4": r"(?:p[0-3]v4|p[1-5]mv4)",
    }
    if not re.fullmatch(patterns[tier], _text(sku.get("name")).lower()):
        return _stop(
            result, "unsupported", "App Service SKU name and tier are inconsistent or unverified."
        )
    result.configuration["sku_name"] = _text(sku["name"]).lower()
    if props.get("hostingEnvironmentProfile") is not None:
        return _stop(
            result, "unsupported", "App Service Environment eligibility is not implemented."
        )
    zonal = props.get("zoneRedundant")
    if type(zonal) is not bool:
        return _stop(result, "unresolved", "Missing or non-boolean App Service zoneRedundant.")
    result.configuration["zone_redundant"] = zonal
    if zonal:
        if tier not in {"premiumv2", "premiumv3", "premiumv4"}:
            return _stop(
                result, "unsupported", "Zone redundancy is not verified for this App Service tier."
            )
        capacity = sku.get("capacity")
        if type(capacity) is not int:
            return _stop(
                result, "unresolved", "Zone-redundant plan requires a known instance count."
            )
        result.configuration["instance_count"] = capacity
        if capacity < 2:
            return _stop(
                result, "ineligible", "Zone-redundant entitlement requires at least two instances."
            )
        if "numberOfWorkers" in props and props["numberOfWorkers"] != capacity:
            return _stop(result, "unresolved", "Conflicting plan instance counts.")
    result.assumptions.append(
        "Plan entitlement applies only to eligible hosted Web/Mobile/API/Logic apps, "
        "not a promise of standalone plan uptime or all apps' simultaneous availability."
    )
    return _resolved(result, "app-service.zone-redundant" if zonal else "app-service.regional")


_DISK_CLASSES = {
    "premium_lrs": "premium",
    "premium_zrs": "premium",
    "premiumv2_lrs": "premium",
    "ultrassd_lrs": "premium",
    "standardssd_lrs": "standard-ssd",
    "standardssd_zrs": "standard-ssd",
    "standard_lrs": "standard-hdd",
}


def _vm(result: ServiceCommitment, resource: dict[str, Any]) -> ServiceCommitment:
    props = _object(resource.get("properties"))
    priority = _text(props.get("priority")).lower()
    if priority in {"spot", "low"}:
        return _stop(
            result,
            "ineligible",
            "Spot/Low-priority VMs have no SLA: "
            "https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms "
            "(retrieved 2026-10-09).",
        )
    if priority != "regular":
        return _stop(
            result, "unresolved", "Missing or unrecognized VM priority; Regular is not inferred."
        )
    result.configuration["priority"] = priority
    for key in ("availabilitySet", "virtualMachineScaleSet", "host", "hostGroup"):
        if props.get(key) is not None:
            return _stop(
                result,
                "unsupported",
                "Availability-set/VMSS/dedicated-host group rules are not implemented.",
            )
    if "availabilitySet" not in props:
        return _stop(
            result, "unresolved", "Single-instance eligibility needs explicit availabilitySet null."
        )
    result.configuration["availability_set"] = None
    profile = _object(props.get("storageProfile"))
    os_disk = _object(profile.get("osDisk"))
    if os_disk.get("vhd") is not None:
        return _stop(result, "unsupported", "Unmanaged OS disk eligibility is not implemented.")
    if "diffDiskSettings" in os_disk and not isinstance(os_disk["diffDiskSettings"], dict):
        return _stop(result, "unresolved", "Malformed OS diffDiskSettings.")
    if _object(os_disk.get("diffDiskSettings")).get("option") is not None:
        return _stop(
            result, "unsupported", "Ephemeral/diff OS disk eligibility is not implemented."
        )
    os_type = _text(_object(os_disk.get("managedDisk")).get("storageAccountType")).lower()
    if not os_type:
        return _stop(
            result,
            "unresolved",
            "Missing managed OS disk storageAccountType; disk IDs are insufficient.",
        )
    if os_type not in _DISK_CLASSES or os_type in {"premiumv2_lrs", "ultrassd_lrs"}:
        return _stop(
            result,
            "unsupported",
            "Unverified OS disk type; Premium v2/Ultra cannot satisfy the OS-disk clause.",
        )
    result.configuration["os_disk_type"] = os_type
    disks = profile.get("dataDisks")
    if not isinstance(disks, list):
        return _stop(result, "unresolved", "Missing dataDisks inventory; no disks is not inferred.")
    disk_types, shares = [], []
    for disk in disks:
        if _object(disk).get("vhd") is not None:
            return _stop(
                result, "unsupported", "Unmanaged data disk eligibility is not implemented."
            )
        managed = _object(_object(disk).get("managedDisk"))
        disk_type = _text(managed.get("storageAccountType")).lower()
        if not disk_type:
            return _stop(result, "unresolved", "Missing managed data disk storageAccountType.")
        if disk_type not in _DISK_CLASSES:
            return _stop(result, "unsupported", "Unmanaged or unverified data disk type.")
        native_facts = _object(
            _object(managed.get("additionalDiskProperties")).get("managedDiskProperties")
        )
        if (
            "maxShares" in managed
            and "maxShares" in native_facts
            and (
                type(managed["maxShares"]) is not int
                or managed["maxShares"] != native_facts["maxShares"]
            )
        ):
            return _stop(result, "unresolved", "Conflicting data disk maxShares eligibility facts.")
        max_shares = managed.get("maxShares", native_facts.get("maxShares"))
        if type(max_shares) is not int:
            return _stop(
                result, "unresolved", "Data disk sharedness needs verified managedDisk.maxShares."
            )
        if max_shares != 1:
            return _stop(
                result, "unsupported", "Shared-disk multi-VM eligibility is not implemented."
            )
        disk_types.append(disk_type)
        shares.append(max_shares)
    result.configuration.update(data_disk_types=disk_types, data_disk_max_shares=shares)
    classes = {_DISK_CLASSES[t] for t in [os_type, *disk_types]}
    lowest = (
        "standard-hdd"
        if "standard-hdd" in classes
        else "standard-ssd"
        if "standard-ssd" in classes
        else "premium"
    )
    return _resolved(result, f"vm.single.{lowest}")


def _sql(result: ServiceCommitment, resource: dict[str, Any]) -> ServiceCommitment:
    props, sku = _object(resource.get("properties")), _object(resource.get("sku"))
    if result.name.lower().rsplit("/", 1)[-1] == "master":
        return _stop(
            result, "unsupported", "System/master database eligibility is not implemented."
        )
    if props.get("elasticPoolId") is not None:
        return _stop(
            result,
            "unresolved",
            "Pooled database requires verified elastic-pool SKU and deployment facts.",
        )
    tier = _tier(
        result,
        sku,
        {
            "basic",
            "standard",
            "premium",
            "generalpurpose",
            "businesscritical",
            "hyperscale",
        },
    )
    if tier is None:
        return result
    patterns = {
        "basic": r"basic",
        "standard": r"s(?:0|1|2|3|4|6|7|9|12)",
        "premium": r"p(?:1|2|4|6|11|15)",
        "generalpurpose": r"gp_(?:s_)?gen5",
        "businesscritical": r"bc_gen5",
        "hyperscale": r"hs_(?:s_)?gen5",
    }
    if not re.fullmatch(patterns[tier], _text(sku.get("name")).lower()):
        return _stop(
            result, "unsupported", "SQL SKU name does not match a verified tier/hardware family."
        )
    result.configuration["sku_name"] = _text(sku["name"]).lower()
    if tier in {"generalpurpose", "hyperscale"}:
        # Auto-paused databases are outside this conservative connection-availability scope.
        auto_pause = props.get("autoPauseDelay")
        if type(auto_pause) is int and auto_pause > 0:
            return _stop(
                result,
                "unsupported",
                "Auto-pausing/serverless database eligibility is not implemented.",
            )
        if "_s_" in _text(sku.get("name")).lower():
            return _stop(
                result, "unsupported", "Serverless SQL database eligibility is not implemented."
            )
    zonal = props.get("zoneRedundant")
    if type(zonal) is not bool:
        return _stop(result, "unresolved", "Missing or non-boolean SQL zoneRedundant.")
    result.configuration["zone_redundant"] = zonal
    if zonal and tier in {"basic", "standard"}:
        return _stop(
            result, "unsupported", "No zone-redundant uplift is verified for this SQL tier."
        )
    return _resolved(result, "sql.zone-redundant" if zonal else "sql.regional")


def _cosmos(result: ServiceCommitment, resource: dict[str, Any]) -> ServiceCommitment:
    props = _object(resource.get("properties"))
    capabilities = props.get("capabilities")
    if not isinstance(capabilities, list) or any(
        not isinstance(c, dict) or not _text(c.get("name")) for c in capabilities
    ):
        return _stop(
            result, "unresolved", "Missing or malformed Cosmos DB capabilities/provisioning mode."
        )
    names = {_text(c["name"]).lower() for c in capabilities}
    if "enableserverless" in names:
        return _stop(
            result,
            "unsupported",
            "Serverless is not covered by the provisioned-throughput SLA tables.",
        )
    if names - {"enablemongo", "enablecassandra", "enablegremlin", "enabletable"}:
        return _stop(result, "unsupported", "Cosmos DB capability variant is not verified.")
    if len(names) > 1:
        return _stop(result, "unsupported", "Conflicting Cosmos DB API capabilities.")
    mode = _text(props.get("capacityMode")).lower()
    if mode == "serverless":
        return _stop(
            result,
            "unsupported",
            "Serverless capacityMode is outside provisioned-throughput tables.",
        )
    if mode != "provisioned":
        return _stop(
            result, "unresolved", "Explicit Cosmos DB capacityMode Provisioned is required."
        )
    result.configuration["capacity_mode"] = mode
    kind = _text(resource.get("kind")).lower()
    if not kind:
        return _stop(result, "unresolved", "Missing Cosmos DB account kind/API.")
    if kind not in {"globaldocumentdb", "mongodb"}:
        return _stop(
            result, "unsupported", "Only the provisioned RU database-account APIs are covered."
        )
    if kind == "mongodb" and names != {"enablemongo"}:
        return _stop(
            result, "unresolved", "MongoDB account kind and API capabilities are inconsistent."
        )
    if kind == "globaldocumentdb" and "enablemongo" in names:
        return _stop(
            result, "unresolved", "GlobalDocumentDB kind conflicts with MongoDB API capability."
        )
    result.configuration.update(account_kind=kind, capabilities=sorted(names))
    multiple = props.get("enableMultipleWriteLocations")
    if type(multiple) is not bool:
        return _stop(result, "unresolved", "Missing or non-boolean enableMultipleWriteLocations.")
    if multiple:
        return _stop(
            result, "unsupported", "Multiple-write-location eligibility is not implemented."
        )
    result.configuration["multiple_write_locations"] = multiple
    consistency = _text(_object(props.get("consistencyPolicy")).get("defaultConsistencyLevel"))
    if consistency.lower() not in {
        "strong",
        "boundedstaleness",
        "session",
        "consistentprefix",
        "eventual",
    }:
        return _stop(result, "unresolved", "Missing or unrecognized Cosmos DB consistency policy.")
    result.configuration["consistency"] = consistency.lower()
    locations = props.get("locations")
    if not isinstance(locations, list) or not locations:
        return _stop(result, "unresolved", "Missing Cosmos DB data-region inventory.")
    if len(locations) != 1:
        return _stop(
            result,
            "unsupported",
            "Multi-region Cosmos DB operation/redundancy eligibility is not implemented.",
        )
    region = _object(locations[0])
    region_name = _text(region.get("locationName")).lower().replace(" ", "")
    if not region_name:
        return _stop(result, "unresolved", "Missing Cosmos DB data-region name.")
    if region_name not in _AZ_REGIONS | _NON_AZ_REGIONS:
        return _stop(
            result,
            "unresolved",
            "Cosmos DB data region is outside the verified pinned region subset.",
        )
    result.configuration["account_regions"] = [region_name]
    zonal = region.get("isZoneRedundant")
    if type(zonal) is not bool:
        return _stop(result, "unresolved", "Missing or non-boolean data-region isZoneRedundant.")
    if zonal and region_name not in _AZ_REGIONS:
        return _stop(
            result, "unsupported", "Cosmos DB zone flag conflicts with verified region AZ support."
        )
    result.configuration["zone_redundant"] = zonal
    return _resolved(
        result,
        "cosmos.single-region-zonal" if zonal else "cosmos.single-region",
    )


def _key_vault(result: ServiceCommitment, resource: dict[str, Any]) -> ServiceCommitment:
    # ARM vault SKUs have name/family, NOT tier; do not require a nonexistent tier.
    props = _object(resource.get("properties"))
    if "sku" in props and not isinstance(props["sku"], dict):
        return _stop(result, "unresolved", "Missing or malformed Key Vault properties.sku.")
    nested_sku = _object(props.get("sku"))
    top_sku = _object(resource.get("sku"))
    if (
        nested_sku
        and top_sku
        and any(
            _text(nested_sku.get(k))
            and _text(top_sku.get(k))
            and _text(nested_sku[k]).lower() != _text(top_sku[k]).lower()
            for k in ("name", "family")
        )
    ):
        return _stop(
            result, "unresolved", "Conflicting top-level and properties.sku Key Vault facts."
        )
    sku = nested_sku or top_sku
    name, family = _text(sku.get("name")).lower(), _text(sku.get("family")).lower()
    if not name or not family:
        return _stop(result, "unresolved", "Missing Key Vault sku.name or sku.family.")
    if name not in {"standard", "premium"} or family != "a":
        return _stop(
            result, "unsupported", "Unverified Key Vault SKU; Managed HSM has a separate SLA."
        )
    result.sku = _text(sku["name"])
    result.configuration.update(sku_name=name, sku_tier=name)
    return _resolved(result, "key-vault.vault")


def _messaging(result: ServiceCommitment, resource: dict[str, Any]) -> ServiceCommitment:
    sku, props = _object(resource.get("sku")), _object(resource.get("properties"))
    tier = _tier(result, sku, {"basic", "standard", "premium"})
    if tier is None:
        return result
    if _text(sku.get("name")).lower() != tier:
        return _stop(result, "unsupported", "Messaging SKU name and tier are inconsistent.")
    result.configuration["sku_name"] = tier
    if result.resource_type == "microsoft.eventhub/namespaces":
        if props.get("clusterArmId") is not None:
            return _stop(
                result,
                "unsupported",
                "Dedicated Event Hubs requires verified cluster membership/SKU.",
            )
        return _resolved(
            result,
            "event-hubs.premium" if tier == "premium" else "event-hubs.basic-standard",
        )
    zonal = _region_zones(result)
    if zonal is None:
        return result
    if not zonal:
        return _resolved(result, "service-bus.non-az-region")
    if tier != "premium":
        return _stop(
            result,
            "unresolved",
            "The pinned legal table does not state a Basic/Standard Queue/Topic rate "
            "for AZ regions; no percentage is extrapolated.",
        )
    partitions = props.get("premiumMessagingPartitions")
    if type(partitions) is not int:
        return _stop(
            result, "unresolved", "Premium AZ uplift needs verified premiumMessagingPartitions."
        )
    result.configuration["premium_messaging_partitions"] = partitions
    if partitions not in {2, 4}:
        return _stop(
            result, "unsupported", "Premium AZ partitioning eligibility requires 2 or 4 partitions."
        )
    return _resolved(result, "service-bus.premium.partitioned-az")


def _aks(result: ServiceCommitment, resource: dict[str, Any]) -> ServiceCommitment:
    sku = _object(resource.get("sku"))
    name, tier = _text(sku.get("name")).lower(), _text(sku.get("tier")).lower()
    if not name or not tier:
        return _stop(result, "unresolved", "Missing AKS sku.name or sku.tier.")
    if name != "base":
        return _stop(
            result,
            "unsupported",
            "AKS Automatic/other cluster SKUs have separate, unimplemented clauses.",
        )
    if tier == "free":
        return _stop(result, "ineligible", "AKS Free tier has no financially backed uptime SLA.")
    if tier not in {"standard", "premium"}:
        return _stop(
            result, "unsupported", "Unverified AKS management tier, including legacy Paid aliases."
        )
    result.configuration.update(sku_name=name, sku_tier=tier, cluster_kind="base")
    zonal = _region_zones(result)
    if zonal is None:
        return result
    result.assumptions.append(
        "AZ-region control-plane distribution is managed automatically per the AKS pricing-tier "
        "documentation; node-pool zones are not evidence of pod/application availability."
    )
    return _resolved(
        result,
        "aks.control-plane.zonal" if zonal else "aks.control-plane.regional",
    )


def resolve_service(resource: dict[str, Any]) -> ServiceCommitment:
    """Resolve an ARG row using pinned rules; no I/O, raw-property echo, or input mutation.

    Missing identity fields stay empty strings; missing eligibility fields are omitted
    from configuration. No default SKU, zones, disk inventory, or deployment is invented.
    Optional operation selectors only scope Storage requests (see module documentation).
    """
    resource_id = _text(resource.get("id"))
    resource_type = _text(resource.get("type")).lower()
    result = ServiceCommitment(
        component_id=resource_id.rstrip("/").lower(),
        resource_id=resource_id,
        name=_text(resource.get("name")),
        resource_type=resource_type,
        location=_text(resource.get("location")).lower(),
        sku=_text(_object(resource.get("sku")).get("name")),
        status="unresolved",
        source_url=SLA_URL if resource_type in _TYPES else "",
        source_version=SOURCE_VERSION if resource_type in _TYPES else "",
    )
    if not resource_type:
        return _stop(result, "unresolved", "Missing resource type.")
    if resource_type not in _TYPES:
        return _stop(
            result, "unsupported", "Resource type has no implemented, verified commitment rule."
        )
    if (
        not resource_id.lower().startswith("/subscriptions/")
        or "/providers/" not in resource_id.lower()
    ):
        return _stop(result, "unresolved", "Missing or malformed scoped Azure resource ID.")
    if not result.name or not result.location:
        return _stop(result, "unresolved", "Missing resource name or location.")
    if "properties" in resource and not isinstance(resource["properties"], dict):
        return _stop(result, "unresolved", "Malformed resource properties.")
    state = _text(_object(resource.get("properties")).get("provisioningState")).lower()
    if state and state != "succeeded":
        return _stop(
            result, "unresolved", "Resource deployment has not reached provisioningState Succeeded."
        )
    if state == "succeeded":
        result.configuration["deployment_state"] = state
    resolvers = {
        "microsoft.storage/storageaccounts": _storage,
        "microsoft.web/serverfarms": _app_service,
        "microsoft.web/sites": _app_service,
        "microsoft.compute/virtualmachines": _vm,
        "microsoft.sql/servers/databases": _sql,
        "microsoft.documentdb/databaseaccounts": _cosmos,
        "microsoft.keyvault/vaults": _key_vault,
        "microsoft.servicebus/namespaces": _messaging,
        "microsoft.eventhub/namespaces": _messaging,
        "microsoft.containerservice/managedclusters": _aks,
    }
    resolver = resolvers.get(resource_type)
    if resolver is None:
        if _text(_object(resource.get("sku")).get("name")).lower() == "basic":
            return _stop(
                result, "unsupported", "Basic Load Balancer eligibility is not implemented."
            )
        return _stop(
            result,
            "unresolved",
            "Load Balancer SLA needs at least two healthy backend VMs and verified configuration; "
            "backend IDs/counts or a zones array do not prove this. No grouping rule is implemented.",
        )
    return resolver(result, resource)
