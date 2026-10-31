"""发布前自检：metadata.yaml 字段、版本号、市场打包体积。

本地运行：python scripts/check_metadata.py
CI：.github/workflows/ci.yml 调用。
"""

import io
import os
import re
import sys
import zipfile

try:
    import yaml
except ImportError:
    sys.exit("需要 pyyaml：pip install pyyaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META = os.path.join(ROOT, "metadata.yaml")
REQUIRED = ("name", "desc", "version", "author")
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
SIZE_LIMIT = 16 * 1024 * 1024
# 市场打包不含 .git 与各类缓存
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}

errors = []


def fail(msg):
    errors.append(msg)


if not os.path.isfile(META):
    fail("缺少 metadata.yaml")
else:
    with open(META, encoding="utf-8") as f:
        meta = yaml.safe_load(f) or {}

    for key in REQUIRED:
        if not meta.get(key):
            fail(f"metadata.yaml 缺少必填字段：{key}")

    name = str(meta.get("name", ""))
    if name and not re.fullmatch(r"[a-z0-9_]+", name):
        fail(f"name 必须是小写英文+下划线：{name!r}")
    if name and not name.startswith("astrbot_plugin_"):
        fail(f"name 建议以 astrbot_plugin_ 开头：{name!r}")

    version = str(meta.get("version", ""))
    if version and not SEMVER.match(version):
        fail(f"version 需符合 x.y.z：{version!r}")
    if re.search(r"(alpha|beta|rc|dev)", version, re.IGNORECASE):
        fail(f"version 不要带预发布后缀（AstrBot 更新检测会混乱）：{version!r}")

    repo = str(meta.get("repo", ""))
    if not repo.startswith("https://github.com/"):
        fail(f"repo 必须是 GitHub https 地址：{repo!r}")

    author = str(meta.get("author", ""))
    if "/" in author:
        fail(f"author 不能含斜杠（市场 key 用 author/name 分层）：{author!r}")

    entry = str(meta.get("entry", "main.py"))
    if not os.path.isfile(os.path.join(ROOT, entry)):
        fail(f"entry 指向的文件不存在：{entry}")

    print(f"name={name}  version={version}  author={author}")
    print(f"display_name={meta.get('display_name', '(未设置)')}")
    print(f"repo={repo}")

if not os.path.isfile(os.path.join(ROOT, "LICENSE")):
    fail("缺少 LICENSE（MIT fork 必须保留原版权声明）")

raw = 0
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            raw += os.path.getsize(path)
            z.write(path, os.path.relpath(path, ROOT))

packed = buf.tell()
print(
    f"仓库 {raw / 1024 / 1024:.2f} MB -> 市场包约 {packed / 1024 / 1024:.2f} MB "
    f"(上限 16.00 MB，余量 {(SIZE_LIMIT - packed) / 1024 / 1024:.2f} MB)"
)
if packed > SIZE_LIMIT:
    fail(f"打包后超过 16MB 上限：{packed / 1024 / 1024:.2f} MB")
elif packed > SIZE_LIMIT * 0.95:
    print("注意：余量不足 5%，上游下次更新可能顶破上限，考虑压缩大资源")

print()
if errors:
    for e in errors:
        print(f"FAIL {e}")
    sys.exit(1)

print("自检通过")
