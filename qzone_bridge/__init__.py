"""QQ空间 AstrBot bridge."""

from __future__ import annotations

import re
from pathlib import Path

# 版本号只在 metadata.yaml 里维护一处。daemon 的兼容性判断是
# `daemon_version == BRIDGE_VERSION`（见 controller._health_payload_is_compatible），
# 两处各写一个版本号的话，只要漏改一边，升级插件后旧 daemon 会一直判定“兼容”、
# 不重启，新代码永远不生效——0.9.1 到 0.9.3 就是这么踩的。
_METADATA_PATH = Path(__file__).resolve().parent.parent / "metadata.yaml"
_FALLBACK_VERSION = "0.9.4"


def _read_metadata_version() -> str:
    """从插件根的 metadata.yaml 读版本号；读不到就退回兜底值。"""

    try:
        text = _METADATA_PATH.read_text(encoding="utf-8-sig")
    except OSError:
        return _FALLBACK_VERSION
    match = re.search(r"^version:\s*[\"']?([^\"'\s#]+)", text, re.MULTILINE)
    return match.group(1) if match else _FALLBACK_VERSION


__version__ = _read_metadata_version()
BRIDGE_API_VERSION = 2026061801
