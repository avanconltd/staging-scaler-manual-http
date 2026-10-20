"""Web-side up/down orchestration, lifted from the staging-web-scaler
ScaleUpScheduled / ScaleDownScheduled timer functions."""
import json
import logging
import os

from azure.identity import DefaultAzureCredential
from azure.mgmt.sql import SqlManagementClient
from azure.mgmt.web import WebSiteManagementClient

from shared_code.web_scaler import scale_sql_database, scale_web_apps_down, scale_web_apps_up

ALARM_APP_NAME = "Func-AlarmNotification-blue-stag-sea"
SQL_UP_DTU = 100
SQL_DOWN_DTU = 10


def _web_app_targets():
    """Windows App Service Plans to scale up/down. Each target is independent:
    an ASP already at the target size is a harmless no-op and a deleted ASP is
    caught per-target without affecting the others. Leave an ASP's env vars
    empty to opt it out of scaling."""
    default_subscription = os.environ.get("AZURE_SUBSCRIPTION_ID")
    return [
        {
            "asp_name": os.environ.get("ASP_NAME"),
            "resource_group": os.environ.get("AZURE_RESOURCE_GROUP"),
            "subscription_id": default_subscription,
            "custom_domain": os.environ.get("CUSTOM_DOMAIN"),
        },
        {
            "asp_name": os.environ.get("ASP_NAME_2"),
            "resource_group": os.environ.get("ASP_RESOURCE_GROUP_2"),
            "subscription_id": os.environ.get("ASP_SUBSCRIPTION_ID_2") or default_subscription,
            "custom_domain": None,
        },
        {
            "asp_name": os.environ.get("ASP_NAME_3"),
            "resource_group": os.environ.get("ASP_RESOURCE_GROUP_3"),
            "subscription_id": os.environ.get("ASP_SUBSCRIPTION_ID_3") or default_subscription,
            "custom_domain": None,
        },
    ]


def _sql_down_targets():
    """SQL databases to scale down. Each target is independent: a target whose
    server or database env var is missing is skipped (not scaled down) without
    affecting the others. resource_group / subscription_id default to
    AZURE_RESOURCE_GROUP / AZURE_SUBSCRIPTION_ID when their own env var is unset."""
    default_subscription = os.environ.get("AZURE_SUBSCRIPTION_ID")
    default_rg = os.environ.get("AZURE_RESOURCE_GROUP")
    specs = [
        ("SQL_SERVER_NAME", "SQL_DATABASE_NAME", "SQL_RESOURCE_GROUP", "SQL_SUBSCRIPTION_ID"),
        ("SQL_SERVER_2", "SQL_DATABASE_2", "SQL_RESOURCE_GROUP_2", "SQL_SUBSCRIPTION_ID_2"),
        ("SQL_SERVER_3", "SQL_DATABASE_3", "SQL_RESOURCE_GROUP_3", "SQL_SUBSCRIPTION_ID_3"),
        ("SQL_SERVER_4", "SQL_DATABASE_4", "SQL_RESOURCE_GROUP_4", "SQL_SUBSCRIPTION_ID_4"),
    ]
    return [
        {
            "server": os.environ.get(server_env),
            "database": os.environ.get(db_env),
            "resource_group": os.environ.get(rg_env) or default_rg,
            "subscription_id": os.environ.get(sub_env) or default_subscription,
        }
        for server_env, db_env, rg_env, sub_env in specs
    ]


def run_web_down() -> dict:
    credential = DefaultAzureCredential()
    summary = {"apps": [], "sql": []}

    # --- Scale down each Windows ASP independently ---
    web_clients = {}  # subscription_id -> WebSiteManagementClient
    for target in _web_app_targets():
        asp_name = target["asp_name"]
        rg = target["resource_group"]
        if not asp_name or not rg:
            logging.warning("web down apps SKIPPED: config not set (asp_name=%s, resource_group=%s)", asp_name, rg)
            summary["apps"].append({"skipped": asp_name, "reason": "config not set"})
            continue

        try:
            subscription_id = target["subscription_id"]
            web_client = web_clients.get(subscription_id)
            if web_client is None:
                web_client = WebSiteManagementClient(credential, subscription_id)
                web_clients[subscription_id] = web_client

            result = scale_web_apps_down(web_client, asp_name, rg, target["custom_domain"])
            summary["apps"].append({"asp_name": asp_name, "resource_group": rg, "apps": result})
            logging.info("web down apps OK (%s): %s", asp_name, json.dumps(result, default=str))
        except Exception as e:
            logging.exception("web down apps FAILED (%s/%s)", rg, asp_name)
            summary["apps"].append({"asp_name": asp_name, "resource_group": rg, "error": str(e)})

    # --- Scale down each SQL database independently ---
    sql_clients = {}  # subscription_id -> SqlManagementClient
    for target in _sql_down_targets():
        server = target["server"]
        database = target["database"]
        rg = target["resource_group"]
        if not server or not database:
            logging.warning("web down sql SKIPPED: config not set (server=%s, database=%s)", server, database)
            summary["sql"].append({"skipped": database or server, "reason": "config not set"})
            continue

        try:
            subscription_id = target["subscription_id"]
            sql_client = sql_clients.get(subscription_id)
            if sql_client is None:
                sql_client = SqlManagementClient(credential, subscription_id)
                sql_clients[subscription_id] = sql_client

            result = scale_sql_database(sql_client, rg, server, database, dtu=SQL_DOWN_DTU)
            summary["sql"].append(result)
            logging.info("web down sql OK (%s/%s): %s", server, database, json.dumps(result, default=str))
        except Exception as e:
            logging.exception("web down sql FAILED (%s/%s)", server, database)
            summary["sql"].append({"server": server, "database": database, "error": str(e)})

    return summary


def run_web_up() -> dict:
    subscription_id = os.environ["AZURE_SUBSCRIPTION_ID"]
    resource_group = os.environ["AZURE_RESOURCE_GROUP"]
    sql_server = os.environ["SQL_SERVER_NAME"]
    sql_database = os.environ["SQL_DATABASE_NAME"]
    alarm_subnet_id = os.environ.get("ALARM_FA_VNET_SUBNET_ID")

    if not alarm_subnet_id:
        logging.warning("ALARM_FA_VNET_SUBNET_ID is not set; VNET reattach for %s will be skipped.", ALARM_APP_NAME)

    credential = DefaultAzureCredential()
    sql_client = SqlManagementClient(credential, subscription_id)

    summary = {"apps": [], "sql": None}

    # --- Scale up each Windows ASP independently (PremiumV3 P0v3, Always On, 64-bit) ---
    web_clients = {}  # subscription_id -> WebSiteManagementClient
    for i, target in enumerate(_web_app_targets()):
        asp_name = target["asp_name"]
        rg = target["resource_group"]
        if not asp_name or not rg:
            logging.warning("web up apps SKIPPED: config not set (asp_name=%s, resource_group=%s)", asp_name, rg)
            summary["apps"].append({"skipped": asp_name, "reason": "config not set"})
            continue

        try:
            target_subscription = target["subscription_id"]
            web_client = web_clients.get(target_subscription)
            if web_client is None:
                web_client = WebSiteManagementClient(credential, target_subscription)
                web_clients[target_subscription] = web_client

            # The AlarmNotification VNET reattach only applies to the primary ASP (ASP_NAME).
            result = scale_web_apps_up(
                web_client,
                asp_name,
                rg,
                alarm_app_subnet_id=alarm_subnet_id if i == 0 else None,
                alarm_app_name=ALARM_APP_NAME,
            )
            summary["apps"].append({"asp_name": asp_name, "resource_group": rg, "apps": result})
            logging.info("web up apps OK (%s): %s", asp_name, json.dumps(result, default=str))
        except Exception as e:
            logging.exception("web up apps FAILED (%s/%s)", rg, asp_name)
            summary["apps"].append({"asp_name": asp_name, "resource_group": rg, "error": str(e)})

    try:
        summary["sql"] = scale_sql_database(sql_client, resource_group, sql_server, sql_database, dtu=SQL_UP_DTU)
        logging.info("web up sql OK: %s", json.dumps(summary["sql"], default=str))
    except Exception as e:
        logging.exception("web up sql FAILED")
        summary["sql"] = {"error": str(e)}

    summary["note"] = "Please integrate VNET with AlarmNotification and Web App"
    return summary
