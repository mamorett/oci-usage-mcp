# oci-usage-mcp

An MCP (Model Context Protocol) server that exposes Oracle Cloud Infrastructure (OCI) cost and usage data as tools, installable via `uvx`.

## Prerequisites

- Python 3.10+
- A valid OCI config at `~/.oci/config` (same as the OCI CLI)
- `uvx` (ships with `uv`: `pip install uv`)

## Installation & usage

### Run directly with uvx (no install needed)

```bash
uvx oci-usage-mcp
```

### Install permanently

```bash
uv tool install oci-usage-mcp
oci-usage-mcp
```

### Install from a local clone

```bash
git clone <repo>
cd oci-usage-mcp
uv tool install .
oci-usage-mcp
```

---

## Claude Desktop / Claude Code integration

Add the following to your MCP client config (e.g. `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "oci-usage": {
      "command": "uvx",
      "args": ["oci-usage-mcp"]
    }
  }
}
```

If your OCI config lives somewhere other than `~/.oci/config`, pass the path via the `OCI_CONFIG_FILE` env var. To use a specific profile, set `OCI_PROFILE_NAME`:

```json
{
  "mcpServers": {
    "oci-usage": {
      "command": "uvx",
      "args": ["oci-usage-mcp"],
      "env": {
        "OCI_CONFIG_FILE": "/path/to/your/oci/config",
        "OCI_PROFILE_NAME": "ADMIN"
      }
    }
  }
}
```

---

## Available tools

| Tool | Description |
|------|-------------|
| `oci_usage_report` | Fetch cost & usage data for the tenancy (last N days, optional service filter, optional verbose OCID resolution) |
| `oci_list_resource_types` | List all monitorable OCI resource types |

### `oci_usage_report` parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `service_filter` | string | — | Filter to a specific service, e.g. `"Compute"` |
| `verbose` | boolean | `false` | Resolve OCIDs to display names (slower) |
| `days` | integer | `30` | How many days back to query (1–365) |

---

## OCI permissions required

The OCI user / instance principal needs at least:

```
Allow group <your-group> to inspect usage-reports in tenancy
Allow group <your-group> to read all-resources in tenancy   # only needed for verbose mode
```
