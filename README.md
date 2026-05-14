# ☁️ OCI Usage & Cost MCP Server

[![MCP](https://img.shields.io/badge/MCP-1.0.0-blue.svg)](https://modelcontextprotocol.io)
[![Python](https://img.shields.io/badge/Python-3.10%2B-green.svg)](https://python.org)

A high-performance [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that empowers AI agents (like Claude) to analyze Oracle Cloud Infrastructure (OCI) cost and usage data. Bridge the gap between your cloud bill and your AI assistant.

---

## ✨ Key Features

- 💰 **Usage Summaries**: Aggregate costs by compartment, service, and SKU.
- 🔍 **Granular Detail**: Optionally resolve OCIDs to human-readable names via the Resource Search API.
- 🌓 **Multi-Profile Support**: Seamlessly switch between different OCI profiles (e.g., Prod, Dev) in a single session.
- ⚡ **Lazy Loading**: Optimized startup time with on-demand OCI client initialization.
- 🚀 **Zero-Config Install**: Run directly via `uvx` with no permanent installation required.

---

## 🛠 Prerequisites

- **Python**: 3.10 or higher.
- **OCI CLI Config**: A valid configuration at `~/.oci/config` (or specified via environment).
- **Tool Runner**: [uv](https://github.com/astral-sh/uv) is highly recommended for the best experience.

---

## 🚀 Quick Start

### 1. Run Instantly (Recommended)
Analyze your tenancy costs without installing a thing:
```bash
uvx oci-usage-mcp
```

### 2. Configure Claude Desktop
Add this to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "oci-usage": {
      "command": "uvx",
      "args": ["oci-usage-mcp"],
      "env": {
        "OCI_CONFIG_FILE": "/path/to/custom/config",
        "OCI_PROFILE_NAME": "DEFAULT"
      }
    }
  }
}
```

---

## 🛠 Available Tools

### 1. `oci_usage_report`
Fetch cost and usage data grouped by compartment, service, and SKU. Fast and efficient.

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `days` | `int` | `30` | Number of days to query (max 365). |
| `service_filter` | `str` | `null` | Filter by service (e.g., "Compute", "Database"). |
| `profile` | `str` | `null` | **Dynamic Profile**: Override default OCI profile. |

### 2. `oci_usage_report_detailed`
Full per-resource detail. Resolves OCIDs to display names using Resource Search.

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `days` | `int` | `30` | Number of days to query. |
| `service_filter` | `str` | `null` | Filter by service. |
| `profile` | `str` | `null` | **Dynamic Profile**: Override default OCI profile. |

### 3. `oci_list_resource_types`
Discover valid service names for filters.

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `profile` | `str` | `null` | **Dynamic Profile**: Override default OCI profile. |

---

## 🔑 Profile Selection Logic

The server resolves the OCI profile in the following order of priority:
1.  **Tool Argument**: The `profile` parameter passed during the tool call.
2.  **Environment Variable**: The `OCI_PROFILE_NAME` set in the server's environment.
3.  **Default**: Fallback to `"DEFAULT"`.

This allows for ultimate flexibility: set a base profile in your config, but ask your AI to "check costs in the 'Admin' profile" on the fly.

---

## 🔐 Permissions Required

Ensure your OCI user or instance principal has the following policies:

```hcl
# Required for all tools
Allow group <your-group> to inspect usage-reports in tenancy

# Required for 'detailed' mode (OCID resolution)
Allow group <your-group> to read all-resources in tenancy
```

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for more information.
