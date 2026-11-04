from __future__ import annotations

import asyncio
import inspect
import json
import types
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pytest

from qzone_bridge.scheduler import cron_delay_seconds
from test_security_hardening import _import_main_with_stubs


def test_cron_delay_applies_offset_only_after_the_base_time() -> None:
    now = datetime(2026, 7, 13, 20, 40, 0)
    calls: list[tuple[int, int]] = []

    def lowest_offset(start: int, end: int) -> int:
        calls.append((start, end))
        return start

    assert cron_delay_seconds("0 21 * * *", 1500, now=now, randint=lowest_offset) == 1200
    assert calls == [(0, 1500)]


def test_daemon_authentication_uses_constant_time_secret_comparison() -> None:
    from qzone_bridge.daemon import create_app

    source = inspect.getsource(create_app)
    assert "hmac.compare_digest(" in source
    assert "supplied_secret == service.state.runtime.secret" not in source


def test_auto_comment_stops_when_login_identity_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    main = _import_main_with_stubs(monkeypatch)
    captured: dict[str, object] = {"detail_calls": 0, "warnings": []}

    class _Logger:
        def debug(self, *args, **kwargs): ...
        def info(self, *args, **kwargs): ...
        def exception(self, *args, **kwargs): ...

        def warning(self, message, *args, **kwargs):
            captured["warnings"].append(message % args if args else str(message))

    class _Controller:
        async def list_feeds(self, **kwargs):
            return {
                "items": [
                    asdict(main.FeedEntry(hostuin=12345, fid="self-post", appid=311, summary="自己的动态"))
                ]
            }

        async def get_status(self, **kwargs):
            raise main.QzoneBridgeError("status unavailable")

        async def detail_feed(self, **kwargs):
            captured["detail_calls"] += 1
            raise AssertionError("identity probe failure must stop before selecting a post")

    async def ready(*args, **kwargs):
        return None

    plugin = object.__new__(main.QzoneStablePlugin)
    plugin.settings = types.SimpleNamespace(comment_latest_count=1, max_feed_limit=20)
    plugin.data_dir = tmp_path
    plugin.controller = _Controller()
    plugin._ensure_cookie_ready = ready
    plugin._ensure_daemon = ready
    monkeypatch.setattr(main, "logger", _Logger())

    asyncio.run(plugin._auto_comment_once())

    assert captured["detail_calls"] == 0
    assert any("cannot determine login_uin" in item for item in captured["warnings"])


@pytest.mark.parametrize("command_name", ["qzone_bind", "qzone_autobind"])
def test_bind_commands_report_status_refresh_failure(
    monkeypatch: pytest.MonkeyPatch,
    command_name: str,
) -> None:
    main = _import_main_with_stubs(monkeypatch)

    class _Event:
        def is_admin(self):
            return True

        def stop_event(self):
            pass

        def plain_result(self, text):
            return text

    class _Controller:
        async def bind_cookie_local(self, cookie):
            return {"daemon_state": "starting", "needs_rebind": True}

    async def auto_bind(*args, **kwargs):
        return {"daemon_state": "starting", "needs_rebind": True}

    async def failed_status():
        raise main.QzoneBridgeError("refresh unavailable")

    plugin = object.__new__(main.QzoneStablePlugin)
    plugin.settings = types.SimpleNamespace(admin_uins=set())
    plugin.controller = _Controller()
    plugin._auto_bind_cookie = auto_bind
    plugin._status_with_recovery = failed_status
    plugin._schedule_publish_render_asset_preload = lambda *args, **kwargs: None
    plugin._schedule_daemon_warmup = lambda *args, **kwargs: None

    async def collect():
        command = getattr(plugin, command_name)
        args = (_Event(), "cookie") if command_name == "qzone_bind" else (_Event(),)
        return [item async for item in command(*args)]

    results = asyncio.run(collect())

    assert len(results) == 1
    assert "Cookie 已绑定，但状态刷新失败" in results[0]
    assert "/qzone status" in results[0]


def test_qzone_list_feed_returns_text_to_the_agent_instead_of_yielding_event_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main = _import_main_with_stubs(monkeypatch)

    class _Event:
        def is_admin(self):
            return True

        def get_self_id(self):
            return 12345

        def plain_result(self, text):
            raise AssertionError("LLM tools must return text instead of sending an event result")

    class _Controller:
        async def list_feeds(self, **kwargs):
            return {
                "items": [
                    asdict(main.FeedEntry(hostuin=12345, fid="fid-1", appid=311, summary="今天很好"))
                ]
            }

    async def ready(*args, **kwargs):
        return None

    plugin = object.__new__(main.QzoneStablePlugin)
    plugin.settings = types.SimpleNamespace(admin_uins=set(), max_feed_limit=20)
    plugin.controller = _Controller()
    plugin._ensure_cookie_ready = ready
    plugin._ensure_daemon = ready

    result = asyncio.run(plugin.tool_list_feed(_Event(), scope="self"))

    assert isinstance(result, str)
    assert "今天很好" in result


def test_all_qzone_llm_tools_are_coroutines_not_async_generators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main = _import_main_with_stubs(monkeypatch)
    tool_names = (
        "tool_get_status",
        "tool_list_feed",
        "tool_detail_feed",
        "tool_view_post",
        "tool_publish_post",
        "tool_comment_post",
        "tool_delete_post",
        "tool_like_post",
    )

    for name in tool_names:
        method = getattr(main.QzoneStablePlugin, name)
        assert inspect.iscoroutinefunction(method), name
        assert not inspect.isasyncgenfunction(method), name


def _to_thread_targets(source: str) -> set[str]:
    import ast

    targets: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "to_thread"
            and node.args
            and isinstance(node.args[0], ast.Name)
        ):
            targets.add(node.args[0].id)
    return targets


def _direct_calls(source: str, names: set[str]) -> set[str]:
    import ast

    tree = ast.parse(source)
    offloaded = {
        id(arg)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "to_thread"
        for arg in node.args
    }
    direct: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in names
            and id(node.func) not in offloaded
        ):
            direct.add(node.func.id)
    return direct


def test_daemon_offloads_blocking_video_helpers_to_threads() -> None:
    """ffprobe / ffmpeg / 远程视频流式下载都是同步阻塞调用，在 daemon 里必须交给线程跑。"""

    import textwrap

    from qzone_bridge import daemon

    helpers = {
        "_probe_video_duration_ms",
        "materialize_video_source_list",
        "materialize_video_cover_list",
        "video_cover_media",
    }
    publish_src = textwrap.dedent(inspect.getsource(daemon.QzoneDaemonService.publish_post))
    native_src = textwrap.dedent(
        inspect.getsource(daemon.QzoneDaemonService._publish_native_video_if_configured)
    )
    h5_src = textwrap.dedent(
        inspect.getsource(daemon.QzoneDaemonService._publish_h5_video_with_visibility_update)
    )

    for source in (publish_src, native_src, h5_src):
        assert not _direct_calls(source, helpers), "有同步调用没走 asyncio.to_thread，会卡住事件循环"

    assert "materialize_video_source_list" in _to_thread_targets(publish_src)
    assert "materialize_video_cover_list" in _to_thread_targets(publish_src)
    assert "_probe_video_duration_ms" in _to_thread_targets(native_src)
    assert "video_cover_media" in _to_thread_targets(h5_src)


def test_describe_llm_failure_maps_common_provider_errors() -> None:
    """供应商报错要能归到具体类别，不能全归成一句“调用失败”。"""

    from qzone_bridge.llm import describe_llm_failure

    cases = (
        ("Error code: 402 - Insufficient Balance (request_id: 5e5165d0)", "余额"),
        ("Error code: 429 - rate limit exceeded", "限流"),
        ("Error code: 401 - Unauthorized: invalid api key", "鉴权"),
        ("Error code: 503 - Service Unavailable", "服务端"),
        ("httpx.ConnectTimeout: connection timed out", "超时"),
        ("openai.BadRequestError: maximum context length is 8192 tokens", "拒绝"),
    )
    for raw, keyword in cases:
        summary, hint = describe_llm_failure(raw)
        assert keyword in summary, (raw, summary)
        assert hint, raw

    summary, hint = describe_llm_failure(RuntimeError("something weird happened"))
    assert summary == "LLM 调用失败"
    assert hint == ""


def test_llm_failure_text_carries_provider_and_raw_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """命令回复里要能看到：结论、建议、用的是哪个 provider、原始错误。"""

    main = _import_main_with_stubs(monkeypatch)
    plugin = object.__new__(main.QzoneStablePlugin)
    plugin.settings = types.SimpleNamespace(news_provider_id="", post_provider_id="deepseek-main")

    text = plugin._llm_failure_text(
        "新闻说说预览",
        RuntimeError("Error code: 402 - Insufficient Balance"),
        "news_provider_id",
        "post_provider_id",
    )

    assert text.startswith("新闻说说预览失败：")
    assert "余额" in text
    assert "建议：" in text
    assert "provider：deepseek-main" in text
    assert "402" in text


def test_news_command_errors_are_not_generic_anymore() -> None:
    """回到“失败。”这种一句话回复就是把原因吞了，这里挡住回退。"""

    source = (
        Path(__file__).resolve().parent.parent.joinpath("main.py").read_text(encoding="utf-8")
    )

    for generic in ("新闻说说预览失败。", "新闻说说发布失败。", "新闻候选获取失败。"):
        assert f'"{generic}"' not in source, generic
    assert source.count("_llm_failure_text(") >= 3
    assert "_llm_empty_text(" in source


def test_bridge_version_tracks_metadata_version() -> None:
    """daemon 兼容判断用 qzone_bridge.__version__，它必须跟着 metadata.yaml 走。"""

    import yaml

    import qzone_bridge

    root = Path(__file__).resolve().parent.parent
    meta = yaml.safe_load(root.joinpath("metadata.yaml").read_text(encoding="utf-8"))

    assert qzone_bridge.__version__ == str(meta["version"])


def test_news_reference_scores_pick_the_news_the_post_is_about() -> None:
    """自动发布的正文提到哪条新闻，就得能认出来是哪条。"""

    from qzone_bridge.news import NewsItem, best_matching_news_item

    items = [
        NewsItem(title="国足客场一比零小胜对手", source="来源甲", item_id="news-1"),
        NewsItem(title="苹果发布会定档下月", source="来源乙", item_id="news-2"),
    ]
    text = "苹果这次发布会定在下个月，又要开始一年一度的挤牙膏了。"

    matched, score = best_matching_news_item(text, items)
    assert matched is not None and matched.item_id == "news-2"
    assert score > 0.15


def test_news_reference_scores_reject_generic_chatter() -> None:
    from qzone_bridge.news import NewsItem, best_matching_news_item

    items = [
        NewsItem(title="苹果发布会定档下月", source="来源甲", item_id="news-1"),
        NewsItem(title="台风今夜登陆广东沿海", source="来源乙", item_id="news-2"),
    ]

    matched, score = best_matching_news_item("今天心情不错，随便说两句。", items)
    assert matched is None
    assert score < 0.15


def _news_plugin(main, tmp_path: Path, candidates: list, generate):
    class _Logger:
        def __init__(self) -> None:
            self.infos: list[str] = []
            self.warnings: list[str] = []

        def debug(self, *args, **kwargs): ...

        def exception(self, *args, **kwargs): ...

        def info(self, message, *args, **kwargs):
            self.infos.append(message % args if args else str(message))

        def warning(self, message, *args, **kwargs):
            self.warnings.append(message % args if args else str(message))

    class _Controller:
        async def publish_post(self, **kwargs):
            return {"fid": "fid-news", "message": "ok"}

    async def fake_ready(*args, **kwargs):
        return None

    async def fake_candidates(**kwargs):
        return list(candidates)

    async def fake_notify(post, payload, message):
        captured["message"] = message

    captured: dict[str, object] = {}
    logger_stub = _Logger()

    plugin = object.__new__(main.QzoneStablePlugin)
    plugin.settings = types.SimpleNamespace(news_once_per_day=False)
    plugin.data_dir = tmp_path
    plugin.controller = _Controller()
    plugin._ensure_cookie_ready = fake_ready
    plugin._ensure_daemon = fake_ready
    plugin._news_candidates = fake_candidates
    plugin._generate_original_news_post_text = generate
    plugin._notify_admin_publish_result = fake_notify
    return plugin, captured, logger_stub


def test_auto_news_publish_records_the_news_it_actually_wrote_about(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """模型自己挑的是第 3 条，就不能把第 1 条记成「参考新闻」。"""

    main = _import_main_with_stubs(monkeypatch)
    candidates = [
        main.NewsItem(title="国足客场一比零小胜对手", source="来源甲", item_id="news-1"),
        main.NewsItem(title="台风今夜登陆广东沿海", source="来源乙", item_id="news-2"),
        main.NewsItem(title="苹果发布会定档下月举行", source="来源丙", item_id="news-3"),
    ]

    async def fake_generate(event, items, **kwargs):
        return "苹果这场发布会定在下月，估计还是老一套的挤牙膏。"

    plugin, captured, logger_stub = _news_plugin(main, tmp_path, candidates, fake_generate)
    monkeypatch.setattr(main, "logger", logger_stub)

    asyncio.run(plugin._auto_news_publish_once())

    state = json.loads((tmp_path / "news_publish_state.json").read_text(encoding="utf-8"))
    assert state["published"][0]["candidate_ids"] == ["news-3"]
    assert state["published"][0]["title"] == "苹果发布会定档下月举行"
    assert "苹果发布会定档下月举行" in str(captured["message"])


def test_auto_news_publish_regenerates_when_text_matches_no_news(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """一条都对不上就指定第一条重写，别把无关的感慨直接发出去。"""

    main = _import_main_with_stubs(monkeypatch)
    candidates = [
        main.NewsItem(title="苹果发布会定档下月举行", source="来源甲", item_id="news-1"),
        main.NewsItem(title="台风今夜登陆广东沿海", source="来源乙", item_id="news-2"),
    ]
    calls: list[dict[str, object]] = []

    async def fake_generate(event, items, **kwargs):
        focus = kwargs.get("focus_item")
        calls.append({"items": items, "focus": focus})
        if focus is not None:
            return "苹果发布会定档下月，估计又是老一套的挤牙膏。"
        return "今天心情不错，随便说两句。"

    plugin, captured, logger_stub = _news_plugin(main, tmp_path, candidates, fake_generate)
    monkeypatch.setattr(main, "logger", logger_stub)

    asyncio.run(plugin._auto_news_publish_once())

    assert len(calls) == 2
    assert calls[1]["focus"] is candidates[0]
    state = json.loads((tmp_path / "news_publish_state.json").read_text(encoding="utf-8"))
    assert state["published"][0]["candidate_ids"] == ["news-1"]
    assert any("regenerating with focus" in line for line in logger_stub.infos)


def test_llm_prompt_passes_empty_contexts_instead_of_typo_key() -> None:
    """``context`` 是拼错的参数名，某些 provider 用 **kwargs 接住后会去读历史对话，
    模型就顺着旧聊天写，正文和新闻完全无关。"""

    source = (
        Path(__file__).resolve().parent.parent.joinpath("qzone_bridge", "llm.py").read_text(encoding="utf-8")
    )
    assert '"context": []' not in source
    assert '"contexts": []' in source
