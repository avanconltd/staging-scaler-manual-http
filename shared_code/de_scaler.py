import logging
import os
from concurrent.futures import ThreadPoolExecutor

from azure.identity import DefaultAzureCredential
from azure.mgmt.web import WebSiteManagementClient

ASPS = [
    {"name": "ASP-batch-blue", "resource_group": "batchRG", "tier": "PremiumV3", "sku": "P0v3"},
    {"name": "ASP-OmniConnectAnomalyDetectionDev", "resource_group": "OmniConnect-Anomaly-Detection", "tier": "PremiumV3", "sku": "P1v3"},
    {"name": "ASP-Scheduled-Exports", "resource_group": "ScheduledExportsRG", "tier": "PremiumV3", "sku": "P1mv3"},
    {"name": "ASP-ZDTDE-Blue-stag-sea", "resource_group": "ZDTDEBlue", "tier": "PremiumV3", "sku": "P2v3"},
    {"name": "BPM-ASP", "resource_group": "OmniConnect-ProcessedTags", "tier": "PremiumV3", "sku": "P0v3"},
    {"name": "BPM-DE-ASP", "resource_group": "BPM-RG", "tier": "PremiumV3", "sku": "P1v3"},
    {"name": "export-asp", "resource_group": "exportresource", "tier": "PremiumV3", "sku": "P0v3"},
    {"name": "fintech-ASP", "resource_group": "RG-DE-Fintech_stag_sea", "tier": "PremiumV3", "sku": "P0v3"},
    {"name": "logchatasp", "resource_group": "logchatdigitallog", "tier": "PremiumV3", "sku": "P0v3"},
    {"name": "queuetriggerjobengine", "resource_group": "queuetriggerjobengine-yousafqadri", "tier": "PremiumV3", "sku": "P0v3"},
    {"name": "Read-Write-API-Service-Plan", "resource_group": "Read-Write-API-Development-Staging", "tier": "PremiumV3", "sku": "P0v3"},
    {"name": "ScheduledPullsRestasp", "resource_group": "PullsRG", "tier": "PremiumV3", "sku": "P0v3"},
    {"name": "ASP-BatchScanner-8861", "resource_group": "queuetriggerTest", "tier": "PremiumV3", "sku": "P0v3"},
    {"name": "Ocfaster-stagging", "resource_group": "OmniConnect-Data-Pipelines", "tier": "Basic", "sku": "B2"},
]


def scale_linux_asp(client, resource_group, asp_name, tier=None, sku_name=None, capacity=None):
    """
    Scale a Linux App Service Plan. If tier and sku_name are provided, scale up.
    If not provided, scale down to Basic B1.
    capacity: Number of instances (optional)
    Returns a result dict; errors are captured, not raised.
    """
    if tier and sku_name:
        target_tier, target_sku = tier, sku_name
    else:
        target_tier, target_sku = "Basic", "B1"
    action = f"to {target_tier} {target_sku}"
    if capacity is not None:
        action += f" with {capacity} instance(s)"

    try:
        asp = client.app_service_plans.get(resource_group, asp_name)
        asp.sku.tier = target_tier
        asp.sku.name = target_sku
        if capacity is not None:
            asp.sku.capacity = capacity
        poller = client.app_service_plans.begin_create_or_update(resource_group, asp_name, asp)
        poller.result()
        logging.info("Scaled ASP '%s' %s successfully.", asp_name, action)
        return {"asp": asp_name, "action": action, "status": "ok"}
    except Exception as e:
        logging.warning("Error scaling ASP '%s': %s", asp_name, e)
        return {"asp": asp_name, "action": action, "status": "error", "error": str(e)}


def _scale_one(client, asp, direction):
    if direction == "up":
        if asp["name"] == "ASP-ZDTDE-Blue-stag-sea":
            return scale_linux_asp(client, asp["resource_group"], asp["name"], tier="PremiumV3", sku_name="P2v3")
        if asp["name"] == "Ocfaster-stagging":
            return scale_linux_asp(client, asp["resource_group"], asp["name"], tier="Basic", sku_name="B2", capacity=3)
        return scale_linux_asp(client, asp["resource_group"], asp["name"], tier=asp["tier"], sku_name=asp["sku"])
    if asp["name"] == "ASP-ZDTDE-Blue-stag-sea":
        return scale_linux_asp(client, asp["resource_group"], asp["name"], tier="PremiumV3", sku_name="P0v3")
    if asp["name"] == "Ocfaster-stagging":
        return scale_linux_asp(client, asp["resource_group"], asp["name"], tier="Basic", sku_name="B1", capacity=1)
    return scale_linux_asp(client, asp["resource_group"], asp["name"])


def scale_all(direction: str) -> list:
    """direction = 'up' or 'down'. Scales all DE ASPs in parallel and returns per-ASP results."""
    direction = direction.lower()
    credential = DefaultAzureCredential()
    client = WebSiteManagementClient(credential, os.environ.get("AZURE_SUBSCRIPTION_ID"))

    with ThreadPoolExecutor(max_workers=len(ASPS)) as pool:
        return list(pool.map(lambda asp: _scale_one(client, asp, direction), ASPS))
