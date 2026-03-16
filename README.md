# Azure Retirement Workbook Fetcher

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3-blue.svg)
![CLI](https://img.shields.io/badge/Interface-CLI-4b8bbe.svg)
![Azure Resource Graph](https://img.shields.io/badge/Azure-Resource%20Graph-0078D4.svg)
![Output](https://img.shields.io/badge/Output-JSON%20%7C%20CSV-5C2D91.svg)

Azure Retirement Workbook 相当のデータを Azure Resource Graph から取得するための小さな Python CLI です。認証には `DefaultAzureCredential` を使い、Azure Advisor に出ているサービス廃止・アップグレード関連の推奨事項を JSON または CSV で出力できます。

軽量な単一ファイル実装で、ローカル調査、棚卸し、CSV エクスポート、独自 KQL の再利用までを最小構成で扱えます。

## Highlights

- Azure Resource Graph に対して既定の KQL を実行し、廃止対象サービスの候補一覧を取得
- 対象サブスクリプションを自動列挙、または `--subscription` で明示指定
- 取得結果を標準出力またはファイルへ JSON / CSV 形式で出力
- 必要に応じて独自の KQL ファイルを渡して別条件で再利用
- `429` と一時的な `5xx` を再試行する堅めの HTTP アクセス

## Quick Start

```powershell
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
az login
python retirement_workbook.py --output-format csv --output-file .\out\retirements.csv
```

## What It Does

この CLI は Azure Resource Graph REST API を直接呼び出し、Advisor のサービス廃止・アップグレード関連データを収集します。

主な用途:

- 退役対象サービスの洗い出し
- Workbook 相当データの CSV 化
- サブスクリプション横断の棚卸し
- 独自 KQL を使ったスポット調査

## Project Layout

実装の中心は [retirement_workbook.py](retirement_workbook.py) です。処理はおおむね次の流れで進みます。

1. `parse_args()` で CLI 引数を受け取る
2. `load_query()` で既定 KQL または外部ファイルの KQL を読み込む
3. `fetch_retirement_workbook_rows()` で認証、対象サブスクリプションの確定、Resource Graph 実行を行う
4. `emit_output()` で JSON または CSV として出力する

主な関数の役割:

- `build_session()`
  `429` や一時的な `5xx` エラーに対して再試行する HTTP セッションを作ります。
- `build_headers()`
  `DefaultAzureCredential` から取得したアクセストークンを使って Azure 管理 API 呼び出し用ヘッダーを作ります。
- `list_accessible_subscription_ids()`
  サブスクリプション未指定時に、現在アクセス可能な Azure サブスクリプションを列挙します。
- `query_resource_graph()`
  Azure Resource Graph をページングしながら全件取得します。`skipToken` を使うため、大きい結果セットでも順次取得できます。
- `emit_output()`
  取得した辞書配列を JSON か CSV に整形して、標準出力またはファイルへ書き出します。

## Default Query

既定の KQL は、Advisor のサービス廃止・アップグレード関連推奨事項を対象にしています。

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

出力には、影響を受けるリソース名、廃止対象機能名、廃止日、成熟度、推奨 ID など、ワークブックで扱いやすい列が含まれます。

## Authentication

`DefaultAzureCredential` は以下のような認証手段を順に利用できます。

- Azure CLI
- Visual Studio Code の Azure サインイン
- サービスプリンシパルの環境変数
- Azure 上での Managed Identity

ローカル開発では、まず次を実行するのが一般的です。

```powershell
az login
```

## Usage Examples

すべてのアクセス可能サブスクリプションを対象に JSON 出力する場合:

```powershell
python retirement_workbook.py
```

特定サブスクリプションだけを対象にする場合:

```powershell
python retirement_workbook.py --subscription 00000000-0000-0000-0000-000000000000 --subscription 11111111-1111-1111-1111-111111111111
```

CSV ファイルへ書き出す場合:

```powershell
python retirement_workbook.py --output-format csv --output-file .\out\retirements.csv
```

独自の KQL ファイルを使う場合:

```powershell
python retirement_workbook.py --query-file .\query.kql --output-file .\out\retirements.json
```

無効状態のサブスクリプションも自動列挙対象に含める場合:

```powershell
python retirement_workbook.py --include-disabled-subscriptions
```

マルチテナントで明示的にテナント ID を渡す場合:

```powershell
python retirement_workbook.py --tenant-id <tenant-id>
```

## Requirements

- Python
- Azure に対して有効な認証コンテキスト
- [requirements.txt](requirements.txt) にある依存関係

```text
azure-identity
requests
```

## Notes

- このスクリプトは Azure Resource Graph REST API を直接呼び出します。
- サブスクリプションを指定しない場合は、先に Azure Resource Manager からアクセス可能なサブスクリプション一覧を取得します。
- HTTP クライアントは `429` と一時的な `5xx` を再試行し、`Retry-After` ヘッダーも尊重します。
- ライセンスは [MIT License](LICENSE) です。
