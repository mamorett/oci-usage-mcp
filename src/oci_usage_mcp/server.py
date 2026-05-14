"""OCI Usage Report MCP Server."""

from datetime import datetime, timedelta
from typing import Any

import mcp.server.stdio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions

# Lazy OCI imports so the server starts even if ~/.oci/config is missing
_oci = None
_config = None
_usage_client = None
_search_client = None


def _get_oci():
    """Lazily initialise OCI clients."""
    global _oci, _config, _usage_client, _search_client
    if _oci is None:
        import oci  # noqa: PLC0415

        _oci = oci
        _config = oci.config.from_file()
        _usage_client = oci.usage_api.UsageapiClient(_config)
        _search_client = oci.resource_search.ResourceSearchClient(_config)
    return _oci, _config, _usage_client, _search_client


# ---------------------------------------------------------------------------
# Business logic (adapted from the original CLI)
# ---------------------------------------------------------------------------

def _get_resource_name(ocid: str) -> str:
    """Look up a human-readable display name for an OCID."""
    try:
        oci, config, _, search_client = _get_oci()
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


def _list_resource_types() -> str:
    """Return all monitorable resource types as a formatted string."""
    oci, config, _, search_client = _get_oci()
    lines: list[str] = [f"{'Resource Type':<50} | Service", "-" * 90]
    try:
        types_response = search_client.list_resource_types(config["tenancy"])
        for t in sorted(types_response.data, key=lambda x: x.name):
            lines.append(f"{t.name:<50} | {t.service_name}")
    except Exception as e:
        lines.append(f"Error fetching resource types: {e}")
    return "\n".join(lines)


def _fetch_usage_items(
    service_filter: str | None,
    include_resource_id: bool,
    days: int,
) -> tuple[list[Any], datetime, datetime] | str:
    """
    Shared pagination logic. Returns (items, start_date, end_date) or an error string.
    """
    oci, config, usage_client, _ = _get_oci()

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    group_by = ["service", "skuName", "compartmentName"]
    if include_resource_id:
        group_by.append("resourceId")

    filter_config = None
    if service_filter:
        filter_config = oci.usage_api.models.Filter(
            operator="IN",
            dimensions=[
                oci.usage_api.models.Dimension(key="service", value=service_filter)
            ],
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


def _get_usage_report(service_filter: str | None = None, days: int = 30) -> str:
    """
    Fetch cost/usage grouped by compartment, service, and SKU.
    Fast — no per-resource API calls.
    """
    result = _fetch_usage_items(service_filter, include_resource_id=False, days=days)
    if isinstance(result, str):
        return result
    all_items, start_date, end_date = result

    if not all_items:
        return "No data found for the specified period / filter."

    lines = [
        f"OCI Usage Report  ({start_date.date()} → {end_date.date()})",
        f"{'Compartment':<30} | {'Service':<30} | {'Cost':>12} | SKU",
        "-" * 100,
    ]
    total_cost = 0.0
    for item in all_items:
        cost = item.computed_amount or 0.0
        total_cost += cost
        lines.append(
            f"{str(item.compartment_name):<30} | {str(item.service):<30} | {cost:>12.4f} | {item.sku_name}"
        )

    currency = all_items[0].currency if all_items else ""
    lines += ["-" * 100, f"TOTAL TENANCY: {total_cost:.4f} {currency}"]
    return "\n".join(lines)


def _get_usage_report_detailed(
    service_filter: str | None = None, days: int = 30
) -> str:
    """
    Fetch cost/usage with per-resource OCID resolution.
    Slower — performs one Resource Search API call per resource.
    """
    result = _fetch_usage_items(service_filter, include_resource_id=True, days=days)
    if isinstance(result, str):
        return result
    all_items, start_date, end_date = result

    if not all_items:
        return "No data found for the specified period / filter."

    lines = [
        f"OCI Usage Report — Detailed  ({start_date.date()} → {end_date.date()})",
        f"{'Compartment':<30} | {'Service':<30} | {'Cost':>12} | {'Resource Name':<40} | OCID",
        "-" * 150,
    ]
    total_cost = 0.0
    for item in all_items:
        cost = item.computed_amount or 0.0
        total_cost += cost
        res_name = (
            _get_resource_name(item.resource_id) if item.resource_id else "N/A"
        )
        lines.append(
            f"{str(item.compartment_name):<30} | {str(item.service):<30} | {cost:>12.4f} | {res_name:<40} | {item.resource_id}"
        )

    currency = all_items[0].currency if all_items else ""
    lines += ["-" * 150, f"TOTAL TENANCY: {total_cost:.4f} {currency}"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MCP server definition
# ---------------------------------------------------------------------------

server = Server("oci-usage-mcp")


_DAYS_SCHEMA = {
    "type": "integer",
    "description": "Number of days back to query. Defaults to 30.",
    "default": 30,
    "minimum": 1,
    "maximum": 365,
}

_SERVICE_SCHEMA = {
    "type": "string",
    "description": (
        "Optional: filter results to a specific OCI service name, "
        "e.g. 'Compute', 'Object Storage', 'Database'. "
        "Use oci_list_resource_types to discover valid names."
    ),
}


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="oci_usage_report",
            description=(
                "Fetch OCI cost and usage data grouped by compartment, service, and SKU. "
                "Fast — no per-resource lookups. Use this for a quick cost overview."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "service_filter": _SERVICE_SCHEMA,
                    "days": _DAYS_SCHEMA,
                },
                "required": [],
            },
        ),
        types.Tool(
            name="oci_usage_report_detailed",
            description=(
                "Fetch OCI cost and usage data with full per-resource detail: resolves "
                "each resource OCID to its human-readable display name via the Resource "
                "Search API. Slower than oci_usage_report — use when you need to identify "
                "specific resources driving costs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "service_filter": _SERVICE_SCHEMA,
                    "days": _DAYS_SCHEMA,
                },
                "required": [],
            },
        ),
        types.Tool(
            name="oci_list_resource_types",
            description=(
                "List all OCI resource types that can be monitored / searched via the "
                "Resource Search API. Useful for discovering valid service names to pass "
                "as service_filter in the other tools."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict[str, Any]
) -> list[types.TextContent]:
    try:
        if name == "oci_usage_report":
            result = _get_usage_report(
                service_filter=arguments.get("service_filter"),
                days=int(arguments.get("days", 30)),
            )
        elif name == "oci_usage_report_detailed":
            result = _get_usage_report_detailed(
                service_filter=arguments.get("service_filter"),
                days=int(arguments.get("days", 30)),
            )
        elif name == "oci_list_resource_types":
            result = _list_resource_types()
        else:
            result = f"Unknown tool: {name}"
    except Exception as e:
        result = f"Error: {e}"

    return [types.TextContent(type="text", text=result)]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import asyncio

    async def _run():
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="oci-usage-mcp",
                    server_version="0.1.0",
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
