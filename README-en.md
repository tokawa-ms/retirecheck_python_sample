# Azure Retirement Workbook Fetcher

[日本語版 README](README.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3-blue.svg)
![CLI](https://img.shields.io/badge/Interface-CLI-4b8bbe.svg)
![Azure Resource Graph](https://img.shields.io/badge/Azure-Resource%20Graph-0078D4.svg)
![Output](https://img.shields.io/badge/Output-JSON%20%7C%20CSV-5C2D91.svg)

This is a small Python CLI that fetches Azure Retirement Workbook-equivalent data from Azure Resource Graph. It uses `DefaultAzureCredential` for authentication and outputs Azure Advisor service retirement and upgrade recommendations as JSON or CSV.

The implementation stays intentionally lightweight in a single file while still covering local investigation, inventory export, CSV generation, and reuse of custom KQL queries.

## Highlights

- Runs the default KQL against Azure Resource Graph to retrieve retirement-related service candidates
- Enumerates target subscriptions automatically, or lets you specify them with `--subscription`
- Writes results to stdout or a file in JSON or CSV format
- Accepts a custom KQL file when you want to reuse the tool for a different filter
- Retries `429` and transient `5xx` responses for more robust HTTP access

## Quick Start

```powershell
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
az login
python retirement_workbook.py --output-format csv --output-file .\out\retirements.csv
```

## What It Does

This CLI calls the Azure Resource Graph REST API directly and collects Advisor data related to service retirement and upgrade recommendations.

Typical uses:

- Identifying services that are nearing retirement
- Exporting workbook-style data to CSV
- Auditing resources across multiple subscriptions
- Running focused investigations with custom KQL

## Project Layout

The implementation is centered in [retirement_workbook.py](retirement_workbook.py). The overall flow is:

1. `parse_args()` receives CLI arguments
2. `load_query()` loads either the default KQL or a KQL file from disk
3. `fetch_retirement_workbook_rows()` authenticates, resolves target subscriptions, and executes the Resource Graph query
4. `emit_output()` writes the result as JSON or CSV

Main function responsibilities:

- `build_session()`
  Creates an HTTP session that retries `429` and transient `5xx` errors.
- `build_headers()`
  Builds headers for Azure management API calls using an access token from `DefaultAzureCredential`.
- `list_accessible_subscription_ids()`
  Lists currently accessible Azure subscriptions when none are specified explicitly.
- `query_resource_graph()`
  Retrieves all pages from Azure Resource Graph. It uses `skipToken`, so larger result sets can be fetched incrementally.
- `emit_output()`
  Formats the list of dictionaries as JSON or CSV and writes it to stdout or a file.

## Default Query

The default KQL targets Advisor recommendations related to service retirement and upgrade events.

```kusto
advisorresources
| where properties.extendedProperties.recommendationSubCategory == "ServiceUpgradeAndRetirement"
| where tostring(properties.category) has "HighAvailability"
| extend resourceId = tolower(tostring(properties.resourceMetadata.resourceId))
| project
		id,
		subscriptionId,
		resourceGroup,
		location,
		resourceId,
		impactedValue = tostring(properties.impactedValue),
		category = tostring(properties.category),
		recommendationSubCategory = tostring(properties.extendedProperties.recommendationSubCategory),
		ServiceID = tostring(properties.recommendationTypeId),
		recommendationOfferingId = tostring(properties.extendedProperties.recommendationOfferingId),
		retirementFeatureName = tostring(properties.extendedProperties.retirementFeatureName),
		retirementDate = tostring(properties.extendedProperties.retirementDate),
		maturityLevel = tostring(properties.extendedProperties.maturityLevel)
```

The output includes workbook-friendly columns such as the affected resource name, retirement feature name, retirement date, maturity level, and recommendation identifiers.

## Authentication

`DefaultAzureCredential` can use the following authentication methods in order:

- Azure CLI
- Azure sign-in from Visual Studio Code
- Service principal environment variables
- Managed identity when running on Azure

For local development, this is the common first step:

```powershell
az login
```

## Usage Examples

Output JSON for all accessible subscriptions:

```powershell
python retirement_workbook.py
```

Target only specific subscriptions:

```powershell
python retirement_workbook.py --subscription 00000000-0000-0000-0000-000000000000 --subscription 11111111-1111-1111-1111-111111111111
```

Write the output to a CSV file:

```powershell
python retirement_workbook.py --output-format csv --output-file .\out\retirements.csv
```

Use a custom KQL file:

```powershell
python retirement_workbook.py --query-file .\query.kql --output-file .\out\retirements.json
```

Include disabled subscriptions in automatic enumeration:

```powershell
python retirement_workbook.py --include-disabled-subscriptions
```

Pass an explicit tenant ID in a multi-tenant environment:

```powershell
python retirement_workbook.py --tenant-id <tenant-id>
```

## Requirements

- Python
- A valid Azure authentication context
- Dependencies listed in [requirements.txt](requirements.txt)

```text
azure-identity
requests
```

## Notes

- This script calls the Azure Resource Graph REST API directly.
- If subscriptions are not provided, it first queries Azure Resource Manager to list accessible subscriptions.
- The HTTP client retries `429` and transient `5xx` responses and respects the `Retry-After` header.
- The project is released under the [MIT License](LICENSE).
