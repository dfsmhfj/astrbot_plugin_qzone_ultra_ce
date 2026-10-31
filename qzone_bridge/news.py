"""News RSS support for scheduled Qzone news posts.

设计要点：
- 内置一组已验证可用的中文 RSS 源（``NEWS_SOURCES``），用户按 id 自行勾选；
- 也允许填入任意可公开访问的 https RSS 地址；
- 多源抓取并发进行，单源失败不影响其它源；
- 混合方式可选：``diverse`` / ``balanced`` / ``weighted`` / ``latest``。
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse
from xml.etree import ElementTree

import httpx

from .errors import QzoneParseError, QzoneRequestError

GOOGLE_NEWS_CHINA_RSS = "https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
GOOGLE_NEWS_WORLD_RSS = "https://news.google.com/rss/headlines/section/topic/WORLD?hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
GOOGLE_NEWS_SEARCH_RSS = "https://news.google.com/rss/search?q={query}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
ITHOME_RSS = "https://www.ithome.com/rss/"

MIX_MODES = ("diverse", "balanced", "weighted", "latest")
DEFAULT_MIX_MODE = "diverse"


@dataclass(frozen=True, slots=True)
class NewsSource:
    """一个可选的新闻源。"""

    id: str
    name: str
    url: str
    category: str
    weight: int = 1
    note: str = ""


# 全部经实测可用（2026-11-01 于目标服务器容器内验证）。
# 36氪 / 品玩 / 虎嗅 / 联合早报 会返回 WAF 拦截页而非 RSS，故不收录。
# 人民网时政源长期未更新（停在 2025-06），已剔除。
NEWS_SOURCES: dict[str, NewsSource] = {
    "ithome": NewsSource("ithome", "IT之家", ITHOME_RSS, "tech", 3, "科技数码，更新最快"),
    "solidot": NewsSource("solidot", "奇客Solidot", "https://www.solidot.org/index.rss", "tech", 2, "科技情报"),
    "geekpark": NewsSource("geekpark", "极客公园", "https://www.geekpark.net/rss", "tech", 2, "科技产业"),
    "ifanr": NewsSource("ifanr", "爱范儿", "https://www.ifanr.com/feed", "tech", 2, "科技生活"),
    "sspai": NewsSource("sspai", "少数派", "https://sspai.com/feed", "tech", 1, "效率与生活"),
    "chinanews": NewsSource(
        "chinanews", "中新网", "https://www.chinanews.com.cn/rss/scroll-news.xml", "news", 3, "综合新闻，含国际"
    ),
    "google_cn": NewsSource("google_cn", "谷歌新闻·中国", GOOGLE_NEWS_CHINA_RSS, "news", 2, "国内通常需要代理"),
    "google_world": NewsSource("google_world", "谷歌新闻·国际", GOOGLE_NEWS_WORLD_RSS, "world", 2, "国内通常需要代理"),
}

_SOURCE_ORDER = {key: index for index, key in enumerate(NEWS_SOURCES)}

# 分组：一个分组可包含多个源，供 scope 方式和默认配置使用。
SOURCE_GROUPS: dict[str, tuple[str, ...]] = {
    "ithome": ("ithome",),
    "china": ("google_cn",),
    "world": ("google_world",),
    "tech": ("ithome", "solidot", "geekpark", "ifanr", "sspai"),
    "news": ("chinanews",),
    "mixed": ("ithome", "solidot", "geekpark", "ifanr", "sspai", "chinanews", "google_cn", "google_world"),
}

NEWS_SCOPE_ALIASES = {
    # 谷歌新闻（旧行为，保持兼容）
    "china": ("china",),
    "cn": ("china",),
    "zh": ("china",),
    "中国": ("china",),
    "国内": ("china",),
    "中国新闻": ("china",),
    "国内新闻": ("china",),
    "world": ("world",),
    "international": ("world",),
    "global": ("world",),
    "国际": ("world",),
    "全球": ("world",),
    "国际新闻": ("world",),
    "世界": ("world",),
    "全球新闻": ("world",),
    # 单源
    "ithome": ("ithome",),
    "it": ("ithome",),
    "itnews": ("ithome",),
    "it之家": ("ithome",),
    "之家": ("ithome",),
    # 科技合集
    "tech": ("tech",),
    "technology": ("tech",),
    "科技": ("tech",),
    "科技新闻": ("tech",),
    "数码": ("tech",),
    "数码新闻": ("tech",),
    "技术": ("tech",),
    "极客": ("tech",),
    # 综合新闻
    "news": ("news",),
    "综合": ("news",),
    "综合新闻": ("news",),
    "要闻": ("news",),
    "社会": ("news",),
    # 全量
    "mixed": ("mixed",),
    "mix": ("mixed",),
    "all": ("mixed",),
    "混合": ("mixed",),
    "混合新闻": ("mixed",),
    "全部": ("mixed",),
    "所有": ("mixed",),
}

# 私有 / 本地主机名后缀，用于阻止自定义 RSS 指向内网。
_PRIVATE_HOST_SUFFIXES = (".local", ".internal", ".localdomain", ".localhost", ".home.arpa")
_PRIVATE_HOST_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
_TRACKING_QUERY_PREFIXES = ("utm_", "spm", "from", "share_token")


@dataclass(slots=True)
class NewsItem:
    title: str
    source: str = ""
    link: str = ""
    published_at: int = 0
    scope: str = ""
    item_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_news_scopes(values: Any) -> list[str]:
    if values is None or values == "":
        raw_items: list[Any] = []
    elif isinstance(values, str):
        raw_items = [item.strip() for item in re.split(r"[,，\s]+", values) if item.strip()]
    elif isinstance(values, (list, tuple, set)):
        raw_items = list(values)
    else:
        raw_items = [values]

    scopes: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        aliases = NEWS_SCOPE_ALIASES.get(str(item or "").strip().lower(), ())
        for scope in aliases:
            if scope not in seen:
                seen.add(scope)
                scopes.append(scope)
    return scopes or ["china"]


def source_label(key: str) -> str:
    """把源 id / scope 转成可读名称，未知的原样返回。"""

    text = str(key or "").strip()
    source = NEWS_SOURCES.get(text)
    if source is not None:
        return source.name
    return text


def _custom_source_key(url: str) -> str:
    host = (urlparse(url).hostname or "custom").lower()
    return host[4:] if host.startswith("www.") else host


def _resolve_source(entry: Any) -> list[tuple[str, str]]:
    """把单个配置项解析成 (源标识, URL) 列表。"""

    text = str(entry or "").strip()
    if not text:
        return []
    lowered = text.lower()
    if lowered in NEWS_SOURCES:
        source = NEWS_SOURCES[lowered]
        return [(source.id, source.url)]
    if lowered in SOURCE_GROUPS:
        result: list[tuple[str, str]] = []
        for key in SOURCE_GROUPS[lowered]:
            source = NEWS_SOURCES.get(key)
            if source is not None:
                result.append((source.id, source.url))
        return result
    if "://" in text:
        return [(_custom_source_key(text), text)]
    return []


def _resolve_scope(scope: str) -> list[tuple[str, str]]:
    if scope in SOURCE_GROUPS:
        return _resolve_source(scope)
    # 未知 scope 默认回退到谷歌中国，保持旧行为
    return [("google_cn", GOOGLE_NEWS_CHINA_RSS)]


def news_rss_urls(
    *,
    scopes: Any = None,
    keywords: list[str] | None = None,
    custom_urls: list[str] | None = None,
    sources: Any = None,
) -> list[tuple[str, str]]:
    """构造待抓取的 RSS 列表。

    ``sources`` 优先于 ``scopes``：前者是用户勾选的源，后者是旧的"新闻范围"。
    """

    urls: list[tuple[str, str]] = []
    selected = [str(item).strip() for item in (sources or []) if str(item).strip()]
    if selected:
        for entry in selected:
            urls.extend(_resolve_source(entry))
    else:
        for scope in normalize_news_scopes(scopes):
            urls.extend(_resolve_scope(scope))
    for keyword in keywords or []:
        text = str(keyword or "").strip()
        if text:
            urls.append((f"keyword:{text}", GOOGLE_NEWS_SEARCH_RSS.format(query=quote_plus(text))))
    for url in custom_urls or []:
        text = str(url or "").strip()
        if text:
            urls.append((_custom_source_key(text), text))
    return _dedupe_urls(urls)


# 兼容旧调用名。
google_news_rss_urls = news_rss_urls


def is_google_news_rss_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme.lower() != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host == "news.google.com" and parsed.path.startswith("/rss")


def _is_publishable_rss_host(host: str) -> bool:
    text = str(host or "").strip().strip(".").lower()
    if not text:
        return False
    if text in _PRIVATE_HOST_NAMES or text.endswith(_PRIVATE_HOST_SUFFIXES):
        return False
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def is_supported_news_rss_url(url: str) -> bool:
    """允许任意 https RSS 源；拒绝 http、无主机名和内网地址。"""

    parsed = urlparse(str(url or "").strip())
    if parsed.scheme.lower() != "https":
        return False
    return _is_publishable_rss_host(parsed.hostname or "")


def _dedupe_urls(urls: list[tuple[str, str]]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for scope, url in urls:
        if url in seen:
            continue
        seen.add(url)
        result.append((scope, url))
    return result


def clean_news_link(link: str) -> str:
    """去掉 utm_* 之类的跟踪参数。"""

    text = str(link or "").strip()
    if not text or "?" not in text:
        return text
    parsed = urlparse(text)
    if not parsed.query:
        return text
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if not any(k.lower().startswith(p) for p in _TRACKING_QUERY_PREFIXES)]
    if len(kept) == len(pairs):
        return text
    return urlunparse(parsed._replace(query=urlencode(kept)))


_SOURCE_NAME_SEPARATORS = ("–", "—", "|", "｜")


def shorten_source_name(name: str) -> str:
    """把「奇客Solidot–传递最新科技情报」这类频道名压成媒体名。"""

    text = re.sub(r"\s+", " ", str(name or "")).strip()
    if not text:
        return ""
    for separator in _SOURCE_NAME_SEPARATORS:
        if separator in text:
            head = text.split(separator, 1)[0].strip()
            if len(head) >= 2:
                return head
    return text


def clean_news_title(title: str, source: str = "") -> str:
    text = re.sub(r"\s+", " ", str(title or "")).strip()
    source_text = str(source or "").strip()
    if source_text and text.endswith(f" - {source_text}"):
        text = text[: -len(source_text) - 3].rstrip()
    return text


# 兼容旧调用名。
clean_google_news_title = clean_news_title


def parse_rss_datetime(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def parse_news_rss(xml_text: str, *, scope: str = "") -> list[NewsItem]:
    """解析 RSS 2.0 文本。

    兼容两类源：
    - Google News / 爱范儿 / 极客公园：item 内带 ``<source>``，标题形如 "标题 - 媒体名"。
    - IT之家 / 中新网 / Solidot 等：item 内没有 ``<source>``，回退用 channel 的 ``<title>``。
    """

    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise QzoneParseError("新闻 RSS 解析失败") from exc

    channel = root.find("channel")
    fallback_source = shorten_source_name(_child_text(channel, "title")) if channel is not None else ""

    items: list[NewsItem] = []
    for item in root.findall(".//channel/item"):
        title = _child_text(item, "title")
        source = _child_text(item, "source") or fallback_source
        title = clean_news_title(title, source)
        link = clean_news_link(_child_text(item, "link") or _child_text(item, "guid"))
        published_at = parse_rss_datetime(_child_text(item, "pubDate"))
        if not title:
            continue
        items.append(
            NewsItem(
                title=title,
                source=source,
                link=link,
                published_at=published_at,
                scope=scope,
                item_id=news_item_id(title=title, source=source, link=link),
            )
        )
    return items


# 兼容旧调用名。
parse_google_news_rss = parse_news_rss


def news_item_id(*, title: str, source: str = "", link: str = "") -> str:
    key = "\n".join([str(link or "").strip(), str(source or "").strip(), str(title or "").strip()])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def filter_recent_news(items: list[NewsItem], *, recency_hours: int, now: datetime | None = None) -> list[NewsItem]:
    hours = int(recency_hours or 0)
    if hours <= 0:
        return list(items)
    current = now or datetime.now(timezone.utc)
    threshold = int(current.timestamp()) - hours * 3600
    return [item for item in items if not item.published_at or item.published_at >= threshold]


def _item_key(item: NewsItem) -> str:
    return item.item_id or news_item_id(title=item.title, source=item.source, link=item.link)


def _group_by_source(items: list[NewsItem], seen_ids: set[str] | None = None) -> dict[str, list[NewsItem]]:
    """按源分组，组内按发布时间倒序，并做全局去重。"""

    buckets: dict[str, list[NewsItem]] = {}
    seen = set(seen_ids or ())
    for item in sorted(items, key=lambda entry: entry.published_at, reverse=True):
        key = _item_key(item)
        if key in seen:
            continue
        seen.add(key)
        buckets.setdefault(item.scope or "unknown", []).append(item)
    return buckets


def _ordered_keys(buckets: dict[str, list[NewsItem]]) -> list[str]:
    return sorted(buckets, key=lambda key: _SOURCE_ORDER.get(key, len(_SOURCE_ORDER)))


def _title_similar(left: str, right: str, threshold: float = 0.72) -> bool:
    a = _normalize_copy_text(left)
    b = _normalize_copy_text(right)
    if len(a) < 6 or len(b) < 6:
        return False
    if a in b or b in a:
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


def _mix_latest(buckets: dict[str, list[NewsItem]], limit: int) -> list[NewsItem]:
    """全局按时间倒序，谁新谁上。"""

    flat = [item for key in _ordered_keys(buckets) for item in buckets[key]]
    flat.sort(key=lambda entry: entry.published_at, reverse=True)
    return flat[:limit]


def _mix_balanced(buckets: dict[str, list[NewsItem]], limit: int) -> list[NewsItem]:
    """轮流从每个源取一条，保证每个源都有代表。"""

    order = _ordered_keys(buckets)
    cursors = {key: 0 for key in order}
    result: list[NewsItem] = []
    while len(result) < limit:
        progressed = False
        for key in order:
            bucket = buckets[key]
            if cursors[key] >= len(bucket):
                continue
            result.append(bucket[cursors[key]])
            cursors[key] += 1
            progressed = True
            if len(result) >= limit:
                break
        if not progressed:
            break
    return result


def _mix_weighted(
    buckets: dict[str, list[NewsItem]], limit: int, weights: dict[str, int] | None = None
) -> list[NewsItem]:
    """按各源权重分配名额。"""

    order = _ordered_keys(buckets)
    weight_map = dict(weights or {})
    resolved = {key: max(1, int(weight_map.get(key, 1))) for key in order}
    total = sum(resolved.values()) or 1
    quotas = {key: max(1, round(limit * resolved[key] / total)) for key in order}

    result: list[NewsItem] = []
    used: set[str] = set()
    for key in order:
        for item in buckets[key][: quotas[key]]:
            result.append(item)
            used.add(_item_key(item))

    if len(result) < limit:
        rest = [
            item
            for key in order
            for item in buckets[key]
            if _item_key(item) not in used
        ]
        rest.sort(key=lambda entry: entry.published_at, reverse=True)
        result.extend(rest[: limit - len(result)])

    result.sort(key=lambda entry: entry.published_at, reverse=True)
    return result[:limit]


def _mix_diverse(buckets: dict[str, list[NewsItem]], limit: int, threshold: float = 0.72) -> list[NewsItem]:
    """轮流取源，并跳过与已选标题过于相似（同一事件）的条目。

    宁可少给几条，也不把同一事件重复堆满候选位，因此结果可能少于 ``limit``。
    """

    order = _ordered_keys(buckets)
    pending = {key: list(buckets[key]) for key in order}
    result: list[NewsItem] = []

    while len(result) < limit:
        progressed = False
        for key in order:
            bucket = pending[key]
            while bucket:
                candidate = bucket.pop(0)
                if any(_title_similar(candidate.title, chosen.title, threshold) for chosen in result):
                    continue
                result.append(candidate)
                progressed = True
                break
            if len(result) >= limit:
                break
        if not progressed:
            break

    return result


def mix_news_items(
    items: list[NewsItem],
    *,
    mode: str = DEFAULT_MIX_MODE,
    limit: int = 12,
    seen_ids: set[str] | None = None,
    weights: dict[str, int] | None = None,
) -> list[NewsItem]:
    """按指定混合方式从多源候选里挑选最终候选列表。"""

    effective_limit = max(1, int(limit or 12))
    buckets = _group_by_source(items, seen_ids)
    if not buckets:
        return []

    key = str(mode or DEFAULT_MIX_MODE).strip().lower()
    if key not in MIX_MODES:
        key = DEFAULT_MIX_MODE

    if key == "latest":
        return _mix_latest(buckets, effective_limit)
    if key == "balanced":
        return _mix_balanced(buckets, effective_limit)
    if key == "weighted":
        resolved = dict({src_id: src.weight for src_id, src in NEWS_SOURCES.items()})
        resolved.update(weights or {})
        return _mix_weighted(buckets, effective_limit, resolved)
    return _mix_diverse(buckets, effective_limit)


def merge_news_items(items: list[NewsItem], *, limit: int = 12, seen_ids: set[str] | None = None) -> list[NewsItem]:
    """兼容旧接口：等价于 ``latest`` 混合方式。"""

    return mix_news_items(items, mode="latest", limit=limit, seen_ids=seen_ids)


def is_news_copy_like(text: str, items: list[NewsItem], *, threshold: float = 0.86) -> bool:
    body = _normalize_copy_text(text)
    if len(body) < 8:
        return False
    for item in items:
        title = _normalize_copy_text(item.title)
        if len(title) < 8:
            continue
        if title in body or body in title:
            return True
        if SequenceMatcher(None, body, title).ratio() >= threshold:
            return True
    return False


class NewsRSSClient:
    def __init__(
        self,
        *,
        timeout: float = 10.0,
        user_agent: str = "",
        trust_env: bool = True,
        max_concurrency: int = 6,
    ) -> None:
        self.timeout = float(timeout or 10.0)
        self.user_agent = user_agent or "Mozilla/5.0"
        self.trust_env = bool(trust_env)
        self.max_concurrency = max(1, int(max_concurrency or 6))

    async def fetch_items(self, urls: list[tuple[str, str]]) -> list[NewsItem]:
        if not urls:
            return []
        for scope, url in urls:
            if not is_supported_news_rss_url(url):
                raise QzoneParseError(
                    f"新闻 RSS 地址不受支持：{url}。"
                    "必须是可公开访问的 https 地址（不支持 http、内网和本地地址）。"
                )

        config = httpx.Timeout(self.timeout)
        limiter = asyncio.Semaphore(self.max_concurrency)
        async with httpx.AsyncClient(
            timeout=config,
            trust_env=self.trust_env,
            headers={"User-Agent": self.user_agent},
            follow_redirects=True,
        ) as client:

            async def fetch_one(scope: str, url: str) -> tuple[list[NewsItem], dict[str, Any] | None]:
                async with limiter:
                    try:
                        response = await client.get(url)
                        response.raise_for_status()
                        return parse_news_rss(response.text, scope=scope), None
                    except (httpx.HTTPError, QzoneParseError) as exc:
                        return [], self._error_detail(exc, url=url, scope=scope)

            results = await asyncio.gather(*(fetch_one(scope, url) for scope, url in urls))

        items: list[NewsItem] = []
        errors: list[dict[str, Any]] = []
        for got, error in results:
            items.extend(got)
            if error is not None:
                errors.append(error)
        if not items and errors:
            raise QzoneRequestError("新闻 RSS 获取失败", detail={"errors": errors[:3], "trust_env": self.trust_env})
        return items

    @staticmethod
    def _error_detail(exc: Exception, *, url: str, scope: str) -> dict[str, Any]:
        message = str(exc).strip()
        if not message:
            cause = getattr(exc, "__cause__", None)
            if cause is not None:
                message = str(cause).strip()
        if not message:
            message = exc.__class__.__name__
        detail: dict[str, Any] = {
            "scope": scope,
            "url": url,
            "message": f"{exc.__class__.__name__}: {message}",
        }
        request = getattr(exc, "request", None)
        if request is not None and getattr(request, "url", None):
            detail["url"] = str(request.url)
        response = getattr(exc, "response", None)
        if response is not None and getattr(response, "status_code", None):
            detail["status_code"] = int(response.status_code)
        return detail


# 兼容旧调用名。
GoogleNewsRSSClient = NewsRSSClient


def _child_text(item: ElementTree.Element, name: str) -> str:
    child = item.find(name)
    return "".join(child.itertext()).strip() if child is not None else ""


def _normalize_copy_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", str(text or "").lower(), flags=re.UNICODE)
