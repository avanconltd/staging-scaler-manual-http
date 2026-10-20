import logging

from azure.mgmt.sql.models import Database, Sku
from azure.mgmt.web.models import SwiftVirtualNetwork


def list_apps_on_asp(client, resource_group, asp_name):
    """Return the apps hosted on an ASP as {"name", "resource_group"} dicts.

    An app can live in a different resource group than its App Service Plan, so
    each app's own resource group is captured (falling back to the ASP's group
    when the API does not report it)."""
    return [
        {
            "name": site.name,
            "resource_group": getattr(site, "resource_group", None) or resource_group,
        }
        for site in client.app_service_plans.list_web_apps(resource_group, asp_name)
    ]


def detach_custom_domain_and_ssl(client, resource_group, app_name, custom_domain):
    try:
        client.web_apps.delete_host_name_binding(resource_group, app_name, custom_domain)
    except Exception:
        pass

    try:
        client.web_apps.delete_host_name_ssl_state(resource_group, app_name, custom_domain)
    except Exception:
        pass


def disconnect_vnet_integration(client, resource_group, app_name):
    try:
        client.web_apps.delete_swift_virtual_network(resource_group, app_name)
    except Exception as e:
        logging.warning("Could not disconnect VNET for %s: %s", app_name, e)


def connect_vnet_integration(client, resource_group, app_name, subnet_id):
    envelope = SwiftVirtualNetwork(subnet_resource_id=subnet_id, swift_supported=True)
    return client.web_apps.create_or_update_swift_virtual_network_connection_with_check(
        resource_group, app_name, envelope
    )


def get_sku_from_dtu(dtu):
    dtu_mapping = {
        10: ("S0", "Standard", 10),
        20: ("S1", "Standard", 20),
        50: ("S2", "Standard", 50),
        100: ("S3", "Standard", 100),
        200: ("S4", "Standard", 200),
        400: ("S6", "Standard", 400),
        800: ("S7", "Standard", 800),
        1600: ("S9", "Standard", 1600),
        3000: ("S12", "Standard", 3000),
        5: ("Basic", "Basic", 5),
    }

    if dtu not in dtu_mapping:
        raise ValueError(f"Invalid DTU value. Supported values: {list(dtu_mapping.keys())}")

    return dtu_mapping[dtu]


def scale_web_apps_up(
    client,
    asp_name,
    resource_group,
    alarm_app_subnet_id=None,
    alarm_app_name="Func-AlarmNotification-blue-stag-sea",
):
    results = []

    if asp_name:
        asp = client.app_service_plans.get(resource_group, asp_name)
        asp.sku.tier = "PremiumV3"
        asp.sku.name = "P0v3"
        client.app_service_plans.begin_create_or_update(resource_group, asp_name, asp).result()

    apps = list_apps_on_asp(client, resource_group, asp_name)
    logging.info("scale_web_apps_up: discovered %d apps on ASP %s", len(apps), asp_name)

    for app in apps:
        app_name, app_rg = app["name"], app["resource_group"]
        try:
            site_config = client.web_apps.get_configuration(app_rg, app_name)
            site_config.always_on = True
            site_config.use_32_bit_worker_process = False
            setattr(site_config, "use32_bit_worker_process", False)
            updated = client.web_apps.update_configuration(app_rg, app_name, site_config)
            bitness_flag = getattr(
                updated, "use_32_bit_worker_process", getattr(updated, "use32_bit_worker_process", False)
            )
            results.append({
                "app_name": app_name,
                "always_on": updated.always_on,
                "bitness": "32" if bitness_flag else "64",
            })
        except Exception as e:
            logging.warning(
                "scale_web_apps_up: skipped app %s (rg=%s): %s", app_name, app_rg, e
            )
            results.append({"app_name": app_name, "error": str(e)})

    if alarm_app_subnet_id:
        try:
            connect_vnet_integration(client, resource_group, alarm_app_name, alarm_app_subnet_id)
            logging.info("VNET attached: app=%s subnet=%s", alarm_app_name, alarm_app_subnet_id)
        except Exception:
            logging.exception("VNET attach failed for %s", alarm_app_name)
            raise

    return results


def scale_web_apps_down(client, asp_name, resource_group, custom_domain):
    results = []

    apps = list_apps_on_asp(client, resource_group, asp_name)
    logging.info("scale_web_apps_down: discovered %d apps on ASP %s", len(apps), asp_name)

    for app in apps:
        app_name, app_rg = app["name"], app["resource_group"]
        try:
            site_config = client.web_apps.get_configuration(app_rg, app_name)
            site_config.always_on = False
            site_config.use_32_bit_worker_process = True
            setattr(site_config, "use32_bit_worker_process", True)
            updated = client.web_apps.update_configuration(app_rg, app_name, site_config)
            bitness_flag = getattr(
                updated, "use_32_bit_worker_process", getattr(updated, "use32_bit_worker_process", False)
            )

            if custom_domain:
                detach_custom_domain_and_ssl(client, app_rg, app_name, custom_domain)
            disconnect_vnet_integration(client, app_rg, app_name)

            results.append({
                "app_name": app_name,
                "always_on": updated.always_on,
                "bitness": "32" if bitness_flag else "64",
            })
        except Exception as e:
            logging.warning(
                "scale_web_apps_down: skipped app %s (rg=%s): %s", app_name, app_rg, e
            )
            results.append({"app_name": app_name, "error": str(e)})

    if asp_name:
        try:
            asp = client.app_service_plans.get(resource_group, asp_name)
            asp.sku.tier = "Free"
            asp.sku.name = "F1"
            client.app_service_plans.begin_create_or_update(resource_group, asp_name, asp).result()
            logging.info("scale_web_apps_down: ASP %s set to Free F1", asp_name)
        except Exception as e:
            logging.warning(
                "scale_web_apps_down: Free F1 rejected for ASP %s (%s); falling back to Basic B1",
                asp_name, e,
            )
            asp = client.app_service_plans.get(resource_group, asp_name)
            asp.sku.tier = "Basic"
            asp.sku.name = "B1"
            client.app_service_plans.begin_create_or_update(resource_group, asp_name, asp).result()
            logging.info("scale_web_apps_down: ASP %s set to Basic B1", asp_name)

    return results


def scale_sql_database(sql_client, resource_group, server_name, database_name, dtu):
    sku_name, tier, capacity = get_sku_from_dtu(dtu)

    current_db = sql_client.databases.get(resource_group, server_name, database_name)
    sku = Sku(name=sku_name, tier=tier, capacity=capacity)
    database_update = Database(location=current_db.location, sku=sku)

    sql_client.databases.begin_update(resource_group, server_name, database_name, database_update)

    current_capacity = current_db.sku.capacity if current_db.sku else 0
    operation_type = (
        "upgrade" if dtu > current_capacity
        else "downgrade" if dtu < current_capacity
        else "no change"
    )

    return {
        "status": "accepted",
        "message": "Database scaling operation has been initiated.",
        "database": database_name,
        "server": server_name,
        "current_sku": (
            f"{current_db.sku.tier} {current_db.sku.name} ({current_db.sku.capacity} DTU)"
            if current_db.sku else "Unknown"
        ),
        "target_sku": f"{tier} {sku_name} ({capacity} DTU)",
        "operation": operation_type,
        "note": "Please wait approximately 10 minutes for the operation to complete.",
    }
