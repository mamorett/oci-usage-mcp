"""OCI Usage Report MCP Server."""

import json
import os
from datetime import datetime, timedelta
from typing import Any, Annotated
from pydantic import Field

from mcp.server.fastmcp import FastMCP

# Lazy OCI imports and client cache
_oci = None
_clients_cache: dict[str, tuple[Any, Any, Any]] = {}


def _get_oci(profile_name: str = "DEFAULT"):
    """Lazily initialise OCI clients for a specific profile."""
    global _oci
    if _oci is None:
        import oci  # noqa: PLC0415
        _oci = oci

    if profile_name in _clients_cache:
        config, usage_client, search_client = _clients_cache[profile_name]
        return _oci, config, usage_client, search_client

    try:
        config_file = os.environ.get("OCI_CONFIG_FILE")
        kwargs = {"profile_name": profile_name}
        if config_file is not None:
            kwargs["file_location"] = config_file

        config = _oci.config.from_file(**kwargs)
        usage_client = _oci.usage_api.UsageapiClient(config)
        search_client = _oci.resource_search.ResourceSearchClient(config)

        _clients_cache[profile_name] = (config, usage_client, search_client)
        return _oci, config, usage_client, search_client
    except Exception:
        # Don't cache failures
        raise


# ---------------------------------------------------------------------------
# Business logic (adapted from the original CLI)
# ---------------------------------------------------------------------------

def _get_resource_name(ocid: str, profile: str) -> str:
    """Look up a human-readable display name for an OCID."""
    try:
        oci, config, _, search_client = _get_oci(profile)
        structured_search = oci.resource_search.models.StructuredSearchDetails(
            query=f"query all resources where identifier = '{ocid}'",
            type="Structured",
        )
        result = search_client.search_resources(structured_search)
        if result.data.items:
            return result.data.items[0].display_name
    except Exception:
        pass
    return "N/A"


def _get_resource_ocid(display_name: str, profile: str) -> str:
    """Look up an OCID for a given human-readable display name."""
    try:
        oci, config, _, search_client = _get_oci(profile)
        structured_search = oci.resource_search.models.StructuredSearchDetails(
            query=f"query all resources where displayName = '{display_name}'",
            type="Structured",
        )
        result = search_client.search_resources(structured_search)
        if result.data.items:
            return result.data.items[0].identifier
    except Exception:
        pass
    return ""


def _list_resource_types(profile: str) -> str:
    """Return all monitorable resource types as a JSON string."""
    try:
        oci, config, _, search_client = _get_oci(profile)
        types_response = search_client.list_resource_types()
        types_list = [t.name for t in sorted(types_response.data, key=lambda x: x.name)]
        return json.dumps({"resource_types": types_list}, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Error fetching resource types: {e}"})


def _fetch_usage_items(
    service_filter: str | None,
    compartments: list[str] | None,
    include_resource_id: bool,
    days: int,
    profile: str,
) -> tuple[list[Any], datetime, datetime] | str:
    """
    Shared pagination logic. Returns (items, start_date, end_date) or an error string.
    """
    try:
        oci, config, usage_client, _ = _get_oci(profile)
    except Exception as e:
        return f"OCI initialization error: {e}"

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    group_by = ["service", "skuName", "compartmentName"]
    if include_resource_id:
        group_by.append("resourceId")

    filters = []
    if service_filter:
        filters.append(
            oci.usage_api.models.Filter(
                operator="IN",
                dimensions=[
                    oci.usage_api.models.Dimension(key="service", value=service_filter)
                ],
            )
        )
        
    if compartments:
        comp_filters = []
        for comp in compartments:
            key = "compartmentId" if _is_ocid(comp) else "compartmentName"
            comp_filters.append(
                oci.usage_api.models.Filter(
                    operator="IN",
                    dimensions=[oci.usage_api.models.Dimension(key=key, value=comp)]
                )
            )
        if len(comp_filters) == 1:
            filters.append(comp_filters[0])
        else:
            filters.append(oci.usage_api.models.Filter(
                operator="OR",
                filters=comp_filters
            ))

    filter_config = None
    if len(filters) == 1:
        filter_config = filters[0]
    elif len(filters) > 1:
        filter_config = oci.usage_api.models.Filter(
            operator="AND",
            filters=filters
        )

    all_items: list[Any] = []
    next_page: str | None = None

    while True:
        request_details = oci.usage_api.models.RequestSummarizedUsagesDetails(
            tenant_id=config["tenancy"],
            time_usage_started=start_date.strftime("%Y-%m-%dT00:00:00.000Z"),
            time_usage_ended=end_date.strftime("%Y-%m-%dT00:00:00.000Z"),
            granularity="MONTHLY",
            query_type="COST",
            group_by=group_by,
            compartment_depth=6,
            filter=filter_config,
        )
        try:
            response = usage_client.request_summarized_usages(
                request_details, page=next_page
            )
            all_items.extend(response.data.items)
            next_page = response.headers.get("opc-next-page")
            if not next_page:
                break
        except Exception as e:
            return f"API call error: {e}"

    return all_items, start_date, end_date


def _is_ocid(value: str) -> bool:
    return value.startswith("ocid1.") or value.startswith("ocid2.")


def _resource_details(item: Any, profile: str) -> tuple[str, str]:
    """Returns (display_name, ocid), looking up missing info if necessary."""
    rn = item.resource_name
    ri = item.resource_id

    display_name = ""
    ocid = ""

    if rn and _is_ocid(rn):
        ocid = rn
    elif ri and _is_ocid(ri):
        ocid = ri

    if rn and not _is_ocid(rn):
        display_name = rn
    elif ri and not _is_ocid(ri):
        display_name = ri

    if ocid and not display_name:
        resolved = _get_resource_name(ocid, profile)
        if resolved != "N/A":
            display_name = resolved

    if display_name and not ocid:
        resolved = _get_resource_ocid(display_name, profile)
        if resolved:
            ocid = resolved

    return display_name or "—", ocid or "—"


def _get_usage_report(service_filter: str | None = None, compartments: list[str] | None = None, days: int = 30, profile: str = "DEFAULT") -> str:
    """
    Fetch cost/usage grouped by compartment, service, and SKU.
    Fast — no per-resource API calls. Returns JSON.
    """
    result = _fetch_usage_items(service_filter, compartments, include_resource_id=False, days=days, profile=profile)
    if isinstance(result, str):
        return json.dumps({"error": result})
    all_items, start_date, end_date = result

    if not all_items:
        return json.dumps({"error": "No data found for the specified period / filter."})

    report = {
        "metadata": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "currency": all_items[0].currency if all_items else "",
            "total_cost": 0.0,
        },
        "items": []
    }

    total_cost = 0.0
    for item in all_items:
        cost = item.computed_amount or 0.0
        total_cost += cost
        report["items"].append({
            "compartment_name": str(item.compartment_name),
            "service": str(item.service),
            "sku_name": str(item.sku_name),
            "cost": cost,
        })

    report["metadata"]["total_cost"] = total_cost
    return json.dumps(report, indent=2)


def _get_usage_report_detailed(
    service_filter: str | None = None, compartments: list[str] | None = None, days: int = 30, profile: str = "DEFAULT"
) -> str:
    """
    Fetch cost/usage with per-resource detail. Returns JSON.
    Resource names come directly from the Usage API where available;
    OCIDs are resolved via Resource Search when needed.
    """
    result = _fetch_usage_items(service_filter, compartments, include_resource_id=True, days=days, profile=profile)
    if isinstance(result, str):
        return json.dumps({"error": result})
    all_items, start_date, end_date = result

    if not all_items:
        return json.dumps({"error": "No data found for the specified period / filter."})

    report = {
        "metadata": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "currency": all_items[0].currency if all_items else "",
            "total_cost": 0.0,
        },
        "items": []
    }

    total_cost = 0.0
    for item in all_items:
        cost = item.computed_amount or 0.0
        total_cost += cost
        res_name, res_id = _resource_details(item, profile)
        report["items"].append({
            "compartment_name": str(item.compartment_name),
            "service": str(item.service),
            "sku_name": str(item.sku_name),
            "resource_name": res_name,
            "resource_id": res_id,
            "cost": cost,
        })

    report["metadata"]["total_cost"] = total_cost
    return json.dumps(report, indent=2)


# ---------------------------------------------------------------------------
# MCP server definition
# ---------------------------------------------------------------------------

mcp = FastMCP("oci-usage-mcp")


@mcp.tool()
def oci_usage_report(
    service_filter: str | None = None,
    compartments: list[str] | None = None,
    days: Annotated[int, Field(ge=1, le=365)] = 30,
    profile: str | None = None,
) -> str:
    """
    Fetch OCI cost and usage data grouped by compartment, service, and SKU.
    Fast — no per-resource lookups. Use this for a quick cost overview.

    Args:
        service_filter: Optional filter results to a specific OCI service name, e.g. 'Compute', 'Object Storage', 'Database'. Use oci_list_resource_types to discover valid names.
        compartments: Optional limit the returned data to a single or list of compartments. You can provide compartment names or OCIDs. If not provided, data for the entire tenancy is returned.
        days: Number of days back to query. Defaults to 30.
        profile: Optional OCI profile name from ~/.oci/config. If not provided, uses OCI_PROFILE_NAME env var, then 'DEFAULT'.
    """
    resolved_profile = profile or os.environ.get("OCI_PROFILE_NAME") or "DEFAULT"
    return _get_usage_report(
        service_filter=service_filter,
        compartments=compartments,
        days=days,
        profile=resolved_profile,
    )


@mcp.tool()
def oci_usage_report_detailed(
    service_filter: str | None = None,
    compartments: list[str] | None = None,
    days: Annotated[int, Field(ge=1, le=365)] = 30,
    profile: str | None = None,
) -> str:
    """
    Fetch OCI cost and usage data with full per-resource detail: resolves
    each resource OCID to its human-readable display name via the Resource
    Search API. Slower than oci_usage_report — use when you need to identify
    specific resources driving costs.

    Args:
        service_filter: Optional filter results to a specific OCI service name, e.g. 'Compute', 'Object Storage', 'Database'. Use oci_list_resource_types to discover valid names.
        compartments: Optional limit the returned data to a single or list of compartments. You can provide compartment names or OCIDs. If not provided, data for the entire tenancy is returned.
        days: Number of days back to query. Defaults to 30.
        profile: Optional OCI profile name from ~/.oci/config. If not provided, uses OCI_PROFILE_NAME env var, then 'DEFAULT'.
    """
    resolved_profile = profile or os.environ.get("OCI_PROFILE_NAME") or "DEFAULT"
    return _get_usage_report_detailed(
        service_filter=service_filter,
        compartments=compartments,
        days=days,
        profile=resolved_profile,
    )


@mcp.tool()
def oci_list_resource_types(profile: str | None = None) -> str:
    """
    List all OCI resource types that can be monitored / searched via the
    Resource Search API. Useful for discovering valid service names to pass
    as service_filter in the other tools.

    Args:
        profile: Optional OCI profile name from ~/.oci/config. If not provided, uses OCI_PROFILE_NAME env var, then 'DEFAULT'.
    """
    resolved_profile = profile or os.environ.get("OCI_PROFILE_NAME") or "DEFAULT"
    return _list_resource_types(profile=resolved_profile)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
