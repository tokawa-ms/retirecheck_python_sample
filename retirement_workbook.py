from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Iterable

import requests
from azure.identity import DefaultAzureCredential
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# Azure Retirement Workbook 相当のデータを取得する既定の KQL。
# Advisor のサービス廃止・アップグレード勧告だけを対象に、
# ワークブックで扱いやすい列へ整形して返す。
DEFAULT_QUERY = """
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
""".strip()

MANAGEMENT_SCOPE = "https://management.azure.com/.default"
MANAGEMENT_ENDPOINT = "https://management.azure.com"
SUBSCRIPTIONS_API_VERSION = "2022-12-01"
RESOURCE_GRAPH_API_VERSION = "2022-10-01"


def build_session() -> requests.Session:
    """Azure 管理 API 呼び出し用の requests.Session を返す。

    429 と一時的な 5xx 応答に対して自動再試行する設定を入れ、
    Azure Resource Manager と Resource Graph の REST 呼び出しを安定化する。

    Returns:
        リトライ付きの HTTP セッション。
    """
    retry = Retry(
        total=5,
        read=5,
        connect=5,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def build_headers(credential: DefaultAzureCredential) -> dict[str, str]:
    """Azure 管理 API に渡す認証ヘッダーを生成する。

    Args:
        credential: アクセストークン取得に使う DefaultAzureCredential。

    Returns:
        Authorization と Content-Type を含むヘッダー辞書。
    """
    token = credential.get_token(MANAGEMENT_SCOPE)
    return {
        "Authorization": f"Bearer {token.token}",
        "Content-Type": "application/json",
    }


def raise_for_status(response: requests.Response) -> None:
    """HTTP 応答を検査し、失敗時は詳細付きで例外を送出する。

    Azure API は本文にエラー詳細を返すことが多いため、単なる HTTPError
    ではなく本文メッセージを含む RuntimeError に変換して扱いやすくする。

    Args:
        response: requests が返した HTTP 応答。

    Raises:
        RuntimeError: 応答本文に Azure 側のエラー詳細が含まれる場合。
        requests.HTTPError: 本文が空で通常の HTTP エラーとして扱う場合。
    """
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text.strip()
        if detail:
            raise RuntimeError(f"Azure API request failed: {detail}") from exc
        raise


def iter_subscriptions(
    session: requests.Session, headers: dict[str, str]
) -> Iterable[dict[str, Any]]:
    """アクセス可能な Azure サブスクリプション情報を順次返す。

    サブスクリプション一覧 API の結果が複数ページに分かれる場合でも、
    nextLink をたどって全件をイテレータとして返す。

    Args:
        session: Azure 管理 API を呼び出す HTTP セッション。
        headers: 認証済みのリクエストヘッダー。

    Yields:
        Azure サブスクリプション 1 件分の辞書。
    """
    next_url = (
        f"{MANAGEMENT_ENDPOINT}/subscriptions?api-version={SUBSCRIPTIONS_API_VERSION}"
    )

    while next_url:
        response = session.get(next_url, headers=headers, timeout=60)
        raise_for_status(response)
        payload = response.json()

        for item in payload.get("value", []):
            yield item

        next_url = payload.get("nextLink")


def list_accessible_subscription_ids(
    session: requests.Session,
    headers: dict[str, str],
    include_all_states: bool = False,
) -> list[str]:
    """問い合わせ対象のサブスクリプション ID 一覧を作る。

    既定では Enabled または Warned 状態のサブスクリプションだけを対象にし、
    必要に応じてそれ以外の状態も含められる。

    Args:
        session: Azure 管理 API を呼び出す HTTP セッション。
        headers: 認証済みのリクエストヘッダー。
        include_all_states: True のとき状態に関係なく列挙する。

    Returns:
        問い合わせ対象となるサブスクリプション ID の一覧。

    Raises:
        RuntimeError: 利用可能なサブスクリプションが 1 件も見つからない場合。
    """
    subscription_ids: list[str] = []

    for subscription in iter_subscriptions(session, headers):
        state = str(subscription.get("state", "")).lower()
        if include_all_states or state in {"enabled", "warned"}:
            subscription_id = subscription.get("subscriptionId")
            if subscription_id:
                subscription_ids.append(subscription_id)

    if not subscription_ids:
        raise RuntimeError("No accessible Azure subscriptions were found.")

    return subscription_ids


def query_resource_graph(
    session: requests.Session,
    headers: dict[str, str],
    query: str,
    subscription_ids: list[str],
    page_size: int = 1000,
) -> list[dict[str, Any]]:
    """Azure Resource Graph に KQL を投げ、結果を全件取得する。

    Resource Graph は skipToken によるページングを返すため、結果が複数ページに
    またがる場合でも最後までたどって 1 つのリストにまとめる。

    Args:
        session: Azure 管理 API を呼び出す HTTP セッション。
        headers: 認証済みのリクエストヘッダー。
        query: 実行する KQL クエリ文字列。
        subscription_ids: クエリ対象のサブスクリプション ID 一覧。
        page_size: 1 回の API 呼び出しで取得する最大件数。上限は 1000。

    Returns:
        Resource Graph が返した各行の辞書リスト。

    Raises:
        RuntimeError: 期待した objectArray 形式で結果が返らなかった場合。
    """
    url = f"{MANAGEMENT_ENDPOINT}/providers/Microsoft.ResourceGraph/resources?api-version={RESOURCE_GRAPH_API_VERSION}"
    rows: list[dict[str, Any]] = []
    skip_token: str | None = None

    while True:
        # skipToken を引き継いで 1000 件ずつ継続取得する。
        body: dict[str, Any] = {
            "subscriptions": subscription_ids,
            "query": query,
            "options": {
                "top": page_size,
                "resultFormat": "objectArray",
            },
        }
        if skip_token:
            body["options"]["skipToken"] = skip_token

        response = session.post(url, headers=headers, json=body, timeout=120)
        raise_for_status(response)
        payload = response.json()

        data = payload.get("data", [])
        if isinstance(data, list):
            rows.extend(data)
        else:
            raise RuntimeError(
                "Unexpected Resource Graph response format. Expected objectArray data."
            )

        skip_token = payload.get("skipToken")
        if not skip_token:
            break

    return rows


def write_csv(rows: list[dict[str, Any]], output_path: Path) -> None:
    """結果行を CSV ファイルへ書き出す。

    列順は最初に見つかったキー順を維持するため、辞書ごとにキー構成が少し
    異なっていても欠損列を含めた CSV を作れる。

    Args:
        rows: 出力対象の辞書リスト。
        output_path: 書き込み先 CSV パス。
    """
    fieldnames: list[str] = []
    seen: set[str] = set()

    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を定義して解析結果を返す。

    Returns:
        subscription、query-file、output-format などの値を持つ Namespace。
    """
    parser = argparse.ArgumentParser(
        description="Fetch Azure Retirement Workbook data from Azure Resource Graph using DefaultAzureCredential.",
    )
    parser.add_argument(
        "--subscription",
        dest="subscriptions",
        action="append",
        help="Subscription ID to query. Repeat this option to pass multiple subscriptions. If omitted, all accessible subscriptions are used.",
    )
    parser.add_argument(
        "--query-file",
        type=Path,
        help="Path to a file that contains a custom KQL query. Defaults to the built-in retirement query.",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        help="Path to write the result. Defaults to stdout.",
    )
    parser.add_argument(
        "--output-format",
        choices=("json", "csv"),
        default="json",
        help="Output format when writing to stdout or a file.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=1000,
        help="Maximum number of records requested per Resource Graph page. The service limit is 1000.",
    )
    parser.add_argument(
        "--include-disabled-subscriptions",
        action="store_true",
        help="Also include subscriptions that are not in Enabled or Warned state when auto-discovering subscriptions.",
    )
    parser.add_argument(
        "--tenant-id",
        help="Optional tenant ID passed to DefaultAzureCredential for multi-tenant scenarios.",
    )
    return parser.parse_args()


def load_query(query_file: Path | None) -> str:
    """実行する KQL 文字列を読み込む。

    Args:
        query_file: 独自 KQL を記述したファイルパス。未指定なら None。

    Returns:
        実行対象の KQL 文字列。
    """
    if not query_file:
        return DEFAULT_QUERY
    return query_file.read_text(encoding="utf-8").strip()


def fetch_retirement_workbook_rows(
    subscriptions: list[str] | None = None,
    query: str | None = None,
    page_size: int = 1000,
    include_disabled_subscriptions: bool = False,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    """Retirement Workbook 相当データの取得処理をまとめて実行する。

    認証情報の組み立て、対象サブスクリプションの決定、Resource Graph への
    クエリ送信までを一括で行い、結果行の一覧を返す。

    Args:
        subscriptions: 明示指定するサブスクリプション ID 一覧。未指定時は自動列挙。
        query: 実行する KQL。未指定時は既定の retirement 用クエリ。
        page_size: Resource Graph の 1 ページ当たり取得件数。
        include_disabled_subscriptions: 自動列挙時に無効状態のサブスクリプションも含めるか。
        tenant_id: DefaultAzureCredential に渡す任意のテナント ID。

    Returns:
        Resource Graph から取得した結果行の一覧。

    Raises:
        ValueError: page_size が 1 から 1000 の範囲外だった場合。
    """
    if not 1 <= page_size <= 1000:
        raise ValueError("page_size must be between 1 and 1000.")

    credential_kwargs: dict[str, Any] = {}
    if tenant_id:
        credential_kwargs["interactive_browser_tenant_id"] = tenant_id

    credential = DefaultAzureCredential(**credential_kwargs)
    session = build_session()
    headers = build_headers(credential)

    # サブスクリプション未指定なら、現在アクセス可能なものを自動列挙する。
    subscription_ids = subscriptions or list_accessible_subscription_ids(
        session=session,
        headers=headers,
        include_all_states=include_disabled_subscriptions,
    )
    return query_resource_graph(
        session=session,
        headers=headers,
        query=query or DEFAULT_QUERY,
        subscription_ids=subscription_ids,
        page_size=page_size,
    )


def emit_output(
    rows: list[dict[str, Any]], output_format: str, output_file: Path | None
) -> None:
    """取得結果を JSON または CSV として出力する。

    Args:
        rows: 出力対象の辞書リスト。
        output_format: json または csv。
        output_file: ファイル出力先。None の場合は標準出力へ書く。
    """
    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        if output_format == "csv":
            write_csv(rows, output_file)
        else:
            output_file.write_text(
                json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        return

    if output_format == "csv":
        fieldnames: list[str] = []
        seen: set[str] = set()
        # 標準出力の CSV も、取得結果に現れた順の列構成を維持する。
        for row in rows:
            for key in row.keys():
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)

        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        return

    json.dump(rows, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def main() -> int:
    """CLI のエントリーポイントを実行する。

    Returns:
        正常終了時は 0。
    """
    args = parse_args()

    rows = fetch_retirement_workbook_rows(
        subscriptions=args.subscriptions,
        query=load_query(args.query_file),
        page_size=args.page_size,
        include_disabled_subscriptions=args.include_disabled_subscriptions,
        tenant_id=args.tenant_id,
    )

    emit_output(rows, args.output_format, args.output_file)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # 詳細は例外メッセージに集約し、CLI では非 0 終了コードを返す。
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
