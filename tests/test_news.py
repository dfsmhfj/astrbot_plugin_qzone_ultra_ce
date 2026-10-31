"""news.py 多源多模式回归测试（离线，mock httpx / errors）。

运行：python tests/test_news.py
CI：.github/workflows/ci.yml 会跑这个文件。
"""

import importlib.util
import os
import sys
import types

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pkg = types.ModuleType("qzone_bridge")
pkg.__path__ = [PLUGIN_DIR]
sys.modules["qzone_bridge"] = pkg

errors = types.ModuleType("qzone_bridge.errors")


class QzoneParseError(Exception):
    def __init__(self, message="", detail=None):
        super().__init__(message)
        self.detail = detail


class QzoneRequestError(Exception):
    def __init__(self, message="", detail=None):
        super().__init__(message)
        self.detail = detail


errors.QzoneParseError = QzoneParseError
errors.QzoneRequestError = QzoneRequestError
sys.modules["qzone_bridge.errors"] = errors

httpx = types.ModuleType("httpx")
httpx.AsyncClient = object
httpx.Timeout = lambda *a, **k: None
httpx.HTTPError = Exception
sys.modules["httpx"] = httpx

spec = importlib.util.spec_from_file_location(
    "qzone_bridge.news", os.path.join(PLUGIN_DIR, "qzone_bridge", "news.py")
)
news = importlib.util.module_from_spec(spec)
sys.modules["qzone_bridge.news"] = news
spec.loader.exec_module(news)

passed = 0
failed = []


def check(label, actual, expected):
    global passed
    if actual == expected:
        passed += 1
    else:
        failed.append(label)
        print(f"  FAIL {label}: 得到 {actual!r}，期望 {expected!r}")


def check_true(label, value):
    global passed
    if value:
        passed += 1
    else:
        failed.append(label)
        print(f"  FAIL {label}: 期望 True，实际 {value!r}")


def titles(items):
    return [i.title for i in items]


print("\n[1] 源注册表完整性")
check_true("8 个内置源", len(news.NEWS_SOURCES) == 8)
for sid in (
    "ithome",
    "solidot",
    "geekpark",
    "ifanr",
    "sspai",
    "chinanews",
    "google_cn",
    "google_world",
):
    check_true(f"源 {sid} 存在", sid in news.NEWS_SOURCES)
check_true(
    "每个源都有 https 地址",
    all(s.url.startswith("https://") for s in news.NEWS_SOURCES.values()),
)
check("source_label(ithome)", news.source_label("ithome"), "IT之家")
check("source_label(chinanews)", news.source_label("chinanews"), "中新网")
check("source_label(未知)", news.source_label("custom"), "custom")

print("\n[2] sources 解析")
r = news.news_rss_urls(sources=["ithome", "chinanews"])
check("两个单源", len(r), 2)
check("顺序与 id 一致", [k for k, _ in r], ["ithome", "chinanews"])
check("ithome url", r[0][1], "https://www.ithome.com/rss/")

r = news.news_rss_urls(sources=["tech"])
check("tech 分组 5 个源", len(r), 5)

r = news.news_rss_urls(sources=["mixed"])
check("mixed 分组 8 个源", len(r), 8)

r = news.news_rss_urls(sources=["https://example.com/feed"])
check("自定义 URL 用域名做标识", r, [("example.com", "https://example.com/feed")])

r = news.news_rss_urls(sources=["https://www.foo.com/rss"])
check("www 前缀被剥掉", r[0][0], "foo.com")

r = news.news_rss_urls(sources=["ithome", "ithome"])
check("重复源去重", len(r), 1)

r = news.news_rss_urls(sources=["不存在的源"])
check("未知 id 被忽略", r, [])

print("\n[3] scopes 回退（sources 为空时）")
r = news.news_rss_urls(scopes=["tech", "news"])
check("tech+news 共 6 个源", len(r), 6)

r = news.news_rss_urls(scopes=["中国"])
check("老中文别名仍走谷歌", r, [("google_cn", news.GOOGLE_NEWS_CHINA_RSS)])

r = news.news_rss_urls(scopes=["ithome"])
check("老 ithome 别名", r, [("ithome", "https://www.ithome.com/rss/")])

r = news.news_rss_urls(sources=["chinanews"], scopes=["中国"])
check("sources 优先于 scopes", len(r), 1)
check("sources 优先时不含谷歌", r[0][0], "chinanews")

print("\n[4] custom_urls / keywords 兼容")
r = news.news_rss_urls(
    sources=["ithome"], custom_urls=["https://www.ithome.com/rss/"], keywords=["AI"]
)
check("custom 与预设同址去重后 2 条（含 keyword）", len(r), 2)
check("keyword scope", r[-1][0], "keyword:AI")

print("\n[5] 链接清洗")
check(
    "去 utm",
    news.clean_news_link(
        "https://www.ifanr.com/1687026?utm_source=rss&utm_medium=rss&utm_campaign="
    ),
    "https://www.ifanr.com/1687026",
)
check(
    "保留正常参数",
    news.clean_news_link("https://example.com/a?id=3"),
    "https://example.com/a?id=3",
)
check(
    "无参数原样",
    news.clean_news_link("https://www.ithome.com/1/020/632.htm"),
    "https://www.ithome.com/1/020/632.htm",
)

print("\n[6] 混合模式")


def mk(title, source, scope, ts, link=""):
    return news.NewsItem(
        title=title,
        source=source,
        link=link or f"https://x/{abs(hash(title))}",
        published_at=ts,
        scope=scope,
        item_id=news.news_item_id(title=title, source=source, link=link or title),
    )


pool = [
    mk("甲源新闻一", "IT之家", "ithome", 100),
    mk("甲源新闻二", "IT之家", "ithome", 99),
    mk("甲源新闻三", "IT之家", "ithome", 98),
    mk("乙源新闻一", "极客公园", "geekpark", 50),
    mk("乙源新闻二", "极客公园", "geekpark", 49),
    mk("乙源新闻三", "极客公园", "geekpark", 48),
    mk("丙源新闻一", "中新网", "chinanews", 10),
    mk("丙源新闻二", "中新网", "chinanews", 9),
    mk("丙源新闻三", "中新网", "chinanews", 8),
]

latest = news.mix_news_items(pool, mode="latest", limit=4)
check(
    "latest 纯时间序",
    titles(latest),
    ["甲源新闻一", "甲源新闻二", "甲源新闻三", "乙源新闻一"],
)

balanced = news.mix_news_items(pool, mode="balanced", limit=4)
check(
    "balanced 轮流取源",
    titles(balanced),
    ["甲源新闻一", "乙源新闻一", "丙源新闻一", "甲源新闻二"],
)

balanced_all = news.mix_news_items(pool, mode="balanced", limit=99)
check("balanced 不超总数", len(balanced_all), 9)
check("balanced 全覆盖", len(set(titles(balanced_all))), 9)

weighted = news.mix_news_items(pool, mode="weighted", limit=8)
w_dist = {}
for i in weighted:
    w_dist[i.scope] = w_dist.get(i.scope, 0) + 1
print(f"       weighted 默认权重分布(limit=8): {w_dist}")
check_true("weighted 拿到 8 条", len(weighted) == 8)
check(
    "weighted 反映内置权重 ithome3:geekpark2:chinanews3",
    w_dist,
    {"ithome": 3, "geekpark": 2, "chinanews": 3},
)

w2 = news.mix_news_items(
    pool, mode="weighted", limit=6, weights={"ithome": 5, "geekpark": 1, "chinanews": 1}
)
d2 = {}
for i in w2:
    d2[i.scope] = d2.get(i.scope, 0) + 1
print(f"       weighted 自定义权重分布: {d2}")
check_true("自定义权重下 ithome 拿满源内全部条目", d2.get("ithome", 0) == 3)
check_true("且占比最高", d2.get("ithome", 0) == max(d2.values()))

print("\n[7] diverse 跨源去重")
dup_pool = [
    mk("苹果官宣国行 Apple 智能接入阿里千问", "IT之家", "ithome", 100),
    mk("国行 Apple 智能官宣：接入阿里千问", "极客公园", "geekpark", 99),
    mk("苹果国行 Apple 智能接入千问官宣", "爱范儿", "ifanr", 98),
    mk("中国首次载人登月计划公布三人乘组", "奇客", "solidot", 97),
    mk("某地发布暴雨橙色预警", "中新网", "chinanews", 96),
]
diverse = news.mix_news_items(dup_pool, mode="diverse", limit=5)
print(f"       diverse 选中: {titles(diverse)}")
apple_count = sum(1 for i in diverse if "Apple 智能" in i.title or "苹果" in i.title)
check("同一事件只留 1 条", apple_count, 1)
check("去重后不补回重复（应为 3 条）", len(diverse), 3)

balanced_dup = news.mix_news_items(dup_pool, mode="balanced", limit=5)
apple_balanced = sum(
    1 for i in balanced_dup if "Apple 智能" in i.title or "苹果" in i.title
)
check_true("balanced 不做过重（对照组应 >1）", apple_balanced > 1)

print("\n[8] seen_ids 与空输入")
filtered = news.mix_news_items(pool, mode="latest", limit=3, seen_ids={pool[0].item_id})
check("seen_ids 生效", "甲源新闻一" not in titles(filtered), True)
check("空输入", news.mix_news_items([], mode="diverse", limit=5), [])
check("非法 mode 回退", len(news.mix_news_items(pool, mode="乱写", limit=2)), 2)

print("\n[9] 向后兼容")
check_true("google_news_rss_urls 别名", news.google_news_rss_urls is news.news_rss_urls)
check_true("GoogleNewsRSSClient 别名", news.GoogleNewsRSSClient is news.NewsRSSClient)
check_true("parse_google_news_rss 别名", news.parse_google_news_rss is news.parse_news_rss)
check(
    "merge_news_items 等价 latest",
    titles(news.merge_news_items(pool, limit=3)),
    titles(latest[:3]),
)
check("is_supported 拒绝内网", news.is_supported_news_rss_url("https://192.168.0.2/rss"), False)
check(
    "is_supported 放行中新网",
    news.is_supported_news_rss_url("https://www.chinanews.com.cn/rss/scroll-news.xml"),
    True,
)

print("\n[10] 来源名压缩")
check("奇客频道名", news.shorten_source_name("奇客Solidot–传递最新科技情报"), "奇客Solidot")
check("竖线分隔", news.shorten_source_name("某某网 | 科技频道"), "某某网")
check("em dash", news.shorten_source_name("某某科技—频道"), "某某科技")
check("无分隔不动", news.shorten_source_name("IT之家"), "IT之家")
check("中新网即时新闻不受影响", news.shorten_source_name("中新网即时新闻"), "中新网即时新闻")
check("空值", news.shorten_source_name(""), "")

print(f"\n{'=' * 56}")
print(f"通过 {passed} 项，失败 {len(failed)} 项")
if failed:
    print("失败：" + ", ".join(failed))
    sys.exit(1)
print("全部通过")
