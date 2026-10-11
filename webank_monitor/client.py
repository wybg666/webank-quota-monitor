"""Read-only adapter for the original unauthenticated product-shelf request.

Only page 1 of MTFUND_PLUS_SUB_DETAIL (at most 100 products) is queried.  This
is not an account API, the complete product catalogue, or an order interface.
No cookies, credentials, login, transactions, notifications, retries, or
authentication workarounds are implemented here.  Python 3.11 standard library.
"""

from __future__ import annotations

import gzip
import http.client
import io
import json
import math
import secrets
import ssl
import urllib.error
import urllib.request
import uuid
import zlib
from datetime import datetime, timezone
from typing import Any


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
PAGE_SIZE = 100
SOURCE = (
    "https://personalv6.webankwealth.com/wm-htrserver/finance/product_shelf/"
    "query_pls_sub_list_no_auth"
)
SCOPE = "公开未登录货架 MTFUND_PLUS_SUB_DETAIL，第 1 页、最多 100 项；并非全部产品或个人可购额度。"

# Keep the original Go client's parameter values and encoded JSON verbatim.
_QUERY_TEMPLATE = (
    "?user_type=0&iid=&terminal_type=iOS&net_type=&app_ver_code=900204"
    "&machine_info=iPhone16%2C2&channel_id=ios&target_type=0&h5_ver=3470"
    "&os_ver=16.8&app_ver=9.2.4&session_type=token&device_id={DEVICE_ID}"
    "&paid={PAID}&oaid=&imei="
    "&param=%7B%22page_num%22%3A1%2C%22page_size%22%3A100%2C%22shelf_type%22%3A%22MTFUND_PLUS_SUB_DETAIL%22%2C%22min_product_period%22%3Anull%2C%22max_product_period%22%3Anull%2C%22bank_short_name_list%22%3Anull%2C%22buy_with_point%22%3Anull%2C%22risk_level_list%22%3Anull%2C%22sale_status_list%22%3Anull%2C%22risk_type%22%3Anull%2C%22sort_factor%22%3A%22SMART%22%2C%22sort_desc%22%3A0%2C%22flush_quota%22%3A1%7D"
)
_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "zh-cn",
    "Accept-Encoding": "gzip",
    "Content-Type": "application/json",
    "native-gm-encrypt": "Y",
    "encrypt_version": "1.0",
    "User-Agent": "WebankApp/2025082119323455 CFNetwork/1240.0.4 Darwin/20.6.0",
    "encrypt": "N",
    "Connection": "keep-alive",
}


class ClientError(Exception):
    """An intentionally short error containing no URLs or upstream messages."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _required_text(value: Any, maximum: int, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise ClientError(f"接口 {field} 字段格式异常。")
    return value


def _quota(value: Any) -> float | None:
    """Missing/null quotas are unknown; booleans and non-finite values fail."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ClientError("接口额度字段不是数值。")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ClientError("接口额度数值超出有效范围。") from None
    if not math.isfinite(number):
        raise ClientError("接口额度包含非有限数值。")
    return number


def parse_response(payload: Any) -> list[dict[str, Any]]:
    """Validate and normalize a decoded response without I/O or mutation.

    The real endpoint returns ret_code and product_code as strings.  A missing
    identifier, duplicate identifier, malformed item, or unexpected schema
    rejects the snapshot instead of manufacturing identifiers or silently
    replacing unknown amounts with zero.  Numeric strings are not coerced.
    """
    if not isinstance(payload, dict):
        raise ClientError("接口响应顶层不是对象。")
    if payload.get("ret_code") != "21400000":
        # Do not display ret_msg: it is untrusted and may contain private data.
        raise ClientError("接口未返回成功状态；未尝试登录或绕过认证。")
    data = payload.get("ret_data")
    if not isinstance(data, dict):
        raise ClientError("接口 ret_data 结构异常。")
    rows = data.get("list")
    if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
        raise ClientError("接口产品列表结构或数量异常。")

    products: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ClientError("接口产品项不是对象。")
        product_id = _required_text(row.get("product_code"), 128, "product_code")
        name = _required_text(row.get("product_name"), 512, "product_name")
        sale_status = _required_text(row.get("sale_status"), 32, "sale_status")
        if product_id in identifiers:
            raise ClientError("接口返回重复产品标识，已拒绝本次快照。")
        identifiers.add(product_id)

        factor = row.get("buy_status_factor")
        if factor is None:
            factor = {}
        if not isinstance(factor, dict):
            raise ClientError("接口 buy_status_factor 结构异常。")
        products.append(
            {
                "id": product_id,
                "name": name,
                "sale_status": sale_status,
                "available_quota": _quota(factor.get("avail_quota")),
                "total_quota": _quota(factor.get("total_quota")),
            }
        )
    return products


def _reject_json_constant(value: str) -> None:
    raise ValueError("non-finite JSON constant")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_limited(stream: Any) -> bytes:
    body = stream.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ClientError("接口响应超过 2 MiB 安全上限。")
    return body


class WebankClient:
    """One read-only request per fetch; device randomness is per instance."""

    def __init__(self, timeout: float = 10):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be a finite positive number")
        try:
            self.timeout = float(timeout)
        except (OverflowError, ValueError):
            raise ValueError("timeout must be a finite positive number") from None
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout must be a finite positive number")
        device_id = str(uuid.uuid4()).upper()
        # Same three 32-hex-part PAID shape; generated locally, never an account ID.
        paid = "-".join(secrets.token_hex(16).upper() for _ in range(3))
        self._url = SOURCE + _QUERY_TEMPLATE.format(DEVICE_ID=device_id, PAID=paid)
        # No CookieJar, HTTP authentication handler, or disabled TLS verification.
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
            _NoRedirect(),
        )

    def _request_payload(self) -> Any:
        request = urllib.request.Request(self._url, headers=_HEADERS, method="GET")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise ClientError("接口 HTTP 状态异常。")
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        length = int(content_length)
                    except (TypeError, ValueError):
                        raise ClientError("接口响应长度声明异常。") from None
                    if length < 0 or length > MAX_RESPONSE_BYTES:
                        raise ClientError("接口响应超过 2 MiB 安全上限。")
                encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
                if encoding not in ("", "identity", "gzip"):
                    raise ClientError("接口使用不支持的内容压缩格式。")
                body = _read_limited(response)
                if encoding == "gzip":
                    # Bound decompressed bytes too; never gzip.decompress an unbounded body.
                    with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                        body = _read_limited(stream)
            try:
                return json.loads(
                    body.decode("utf-8"),
                    parse_constant=_reject_json_constant,
                    object_pairs_hook=_unique_json_object,
                )
            except (UnicodeError, ValueError, RecursionError):
                raise ClientError("接口未返回有效 UTF-8 JSON 数据。") from None
        except ClientError:
            raise
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if 300 <= status < 400:
                raise ClientError("接口要求重定向，已拒绝跟随。") from None
            if status in (401, 403):
                raise ClientError("接口拒绝访问；未尝试登录或绕过认证。") from None
            if status == 429:
                raise ClientError("接口请求过于频繁，请降低刷新频率。") from None
            raise ClientError(f"接口 HTTP 请求失败（{status}）。") from None
        except (urllib.error.URLError, OSError, EOFError, zlib.error, http.client.HTTPException):
            raise ClientError("公开接口连接或响应读取失败。") from None

    def fetch(self) -> dict[str, Any]:
        payload = self._request_payload()
        products = parse_response(payload)
        warnings = ["仅供查看公开货架；额度及销售状态以银行官方页面实时结果为准。"]
        unknown = sum(product["available_quota"] is None for product in products)
        unknown_total = sum(product["total_quota"] is None for product in products)
        if unknown:
            warnings.append(f"{unknown} 项未提供可用额度，显示为未知，不按 0 处理。")
        if unknown_total:
            warnings.append(f"{unknown_total} 项未提供总额度，显示为未知，不按 0 处理。")
        data = payload["ret_data"]
        if data.get("has_more") not in (None, False, 0, "0", "false") or len(products) == PAGE_SIZE:
            warnings.append("该货架可能还有后续页；本工具只读取第 1 页。")
        return {
            "products": products,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": SOURCE,
            "warnings": warnings,
            "scope": SCOPE,
        }
