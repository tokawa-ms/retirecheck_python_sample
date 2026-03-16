# Azure Retirement Workbook Fetcher

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3-blue.svg)
![CLI](https://img.shields.io/badge/Interface-CLI-4b8bbe.svg)
![Azure Resource Graph](https://img.shields.io/badge/Azure-Resource%20Graph-0078D4.svg)
![Output](https://img.shields.io/badge/Output-JSON%20%7C%20CSV-5C2D91.svg)

This is a small Python CLI for retrieving Azure Retirement Workbook-equivalent data from Azure Resource Graph. It uses `DefaultAzureCredential` for authentication and outputs Azure Advisor service retirement and upgrade recommendations in JSON or CSV format.

With a lightweight single-file implementation, it covers local investigation, inventorying, CSV export, and reuse of custom KQL with a minimal setup.

## Highlights

- Runs the built-in KQL against Azure Resource Graph to retrieve candidate services that are approaching retirement
- Automatically enumerates target subscriptions, or accepts explicit subscription IDs with `--subscription`
- Outputs results in JSON or CSV to stdout or a file
- Accepts a custom KQL file so the tool can be reused for different conditions
- Uses resilient HTTP access with retries for `429` and transient `5xx` responses

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

Typical use cases:

- Identifying services that are scheduled for retirement
- Exporting workbook-equivalent data to CSV
- Cross-subscription inventory reviews
- Spot investigations with custom KQL

## Project Layout

The core implementation lives in [retirement_workbook.py](retirement_workbook.py). The overall flow is roughly:

1. Receive CLI arguments in `parse_args()`
2. Load the built-in KQL or an external KQL file in `load_query()`
3. Authenticate, determine target subscriptions, and execute Resource Graph in `fetch_retirement_workbook_rows()`
4. Output the results as JSON or CSV in `emit_output()`

Main functions:

- `build_session()`
  Creates an HTTP session that retries `429` and transient `5xx` errors.
- `build_headers()`
  Builds headers for Azure management API calls using an access token obtained from `DefaultAzureCredential`.
- `list_accessible_subscription_ids()`
  Enumerates the Azure subscriptions currently accessible when subscriptions are not specified explicitly.
- `query_resource_graph()`
  Retrieves all Resource Graph results while handling pagination. Because it uses `skipToken`, it can process large result sets incrementally.
- `emit_output()`
  Formats the retrieved list of dictionaries as JSON or CSV and writes it to stdout or a file.

## Default Query

The built-in KQL targets Advisor recommendations related to service retirement and upgrades.

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

The output includes workbook-friendly columns such as the impacted resource name, retirement feature name, retirement date, maturity level, and recommendation IDs.

## Authentication

`DefaultAzureCredential` can use the following authentication methods in order:

- Azure CLI
- Azure sign-in from Visual Studio Code
- Service principal environment variables
- Managed Identity on Azure

For local development, the typical first step is:

```powershell
az login
```

## Usage Examples

Output JSON for all accessible subscriptions:

```powershell
python retirement_workbook.py
```

Query only specific subscriptions:

```powershell
python retirement_workbook.py --subscription 00000000-0000-0000-0000-000000000000 --subscription 11111111-1111-1111-1111-111111111111
```

Write output to a CSV file:

```powershell
python retirement_workbook.py --output-format csv --output-file .\out\retirements.csv
```

Use a custom KQL file:

```powershell
python retirement_workbook.py --query-file .\query.kql --output-file .\out\retirements.json
```

Also include disabled subscriptions in automatic enumeration:

```powershell
python retirement_workbook.py --include-disabled-subscriptions
```

Pass an explicit tenant ID in a multi-tenant setup:

```powershell
python retirement_workbook.py --tenant-id <tenant-id>
```

## Requirements

- Python
- A valid authentication context for Azure
- The dependencies listed in [requirements.txt](requirements.txt)

```text
azure-identity
requests
```

## Notes

- This script calls the Azure Resource Graph REST API directly.
- If subscriptions are not specified, it first retrieves the list of accessible subscriptions from Azure Resource Manager.
- The HTTP client retries `429` and transient `5xx` responses and also respects the `Retry-After` header.
- The license is the [MIT License](LICENSE).
