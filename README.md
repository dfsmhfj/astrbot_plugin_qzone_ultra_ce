# QQ空间Ultra CE

> 基于 [diaomin66/astrbot_plugin_qzone_ultra](https://github.com/diaomin66/astrbot_plugin_qzone_ultra) 的独立发布版，由 [@DFSMHFJ](https://github.com/DFSMHFJ) 维护与发布。
> 插件标识符 `astrbot_plugin_qzone_ultra_ce`，配置和数据挂在自己名下，与原版互不覆盖。

**本版相对原版有两处变化：新闻说说从「只能读 Google News 的两个固定 URL」扩展成多源 RSS；daemon 视频发布链路里的同步阻塞调用改走线程。** 其余模块保持原样。

要求 AstrBot `>=4.16,<5`，优先适配 OneBot v11 / aiocqhttp。

问题反馈走 [GitHub Issues](https://github.com/DFSMHFJ/astrbot_plugin_qzone_ultra_ce/issues)。CE 版的问题请提到这里，不要打扰原作者。

> ⚠️ **装了本版就停用或卸载原版**。两者命令组同名（`/qzone`、`/新闻说说`…）、daemon 同端口，同时启用只会互相抢命令，没有任何共存价值。从原版切过来见「从上游迁移」。

---

## 多源 RSS 新闻说说（本版新增）

原版的 `news.py` 只认两个 Google News 固定地址，国内不挂代理基本拉不到东西。本版改成源注册表，从 8 个源里任选。

### 内置源

| id | 名称 | 分类 | 权重 | 备注 |
| --- | --- | --- | --- | --- |
| `ithome` | IT之家 | 科技 | 3 | 更新最快，单次约 60 条 |
| `solidot` | 奇客Solidot | 科技 | 2 | 科技情报 |
| `geekpark` | 极客公园 | 科技 | 2 | 科技产业 |
| `ifanr` | 爱范儿 | 科技 | 2 | 科技生活 |
| `sspai` | 少数派 | 科技 | 1 | 效率与生活 |
| `chinanews` | 中新网 | 综合 | 3 | 即时新闻，含国际 |
| `google_cn` | 谷歌新闻·中国 | 综合 | 2 | 国内需代理 |
| `google_world` | 谷歌新闻·国际 | 国际 | 2 | 国内需代理 |

前 6 个国内源 2026-11-01 实测直连可用，一次抓到 170 条，36 小时内 112 条。两个谷歌源国内需要代理，没配代理时会连接失败，不影响其他源。

### 怎么选源

`news.sources` 优先于 `news.scopes`。三种写法都行：

- **源 id**：`["ithome", "sspai"]`
- **分组名**：`tech`（5 个科技源）、`news`（中新网）、`mixed`（全部 8 个）、`ithome`、`china`、`world`
- **自定义地址**：任意 `https://` 开头的 RSS 2.0 地址

留空则按 `news.scopes` 取源，默认 `["tech", "news"]`。

### 四种混合策略

`news.mix_mode` 控制从多源候选里怎么挑。同一批真实数据（12 条候选）跑出来的结果：

| 模式 | 行为 | 实际分布 |
| --- | --- | --- |
| `diverse`（默认） | 轮流取源，并过滤标题高度相似的同事件 | 6 源各 2 条，0 重复 |
| `balanced` | 轮流取源，保证每个源都有代表，不做过重过滤 | 6 源各 2 条 |
| `weighted` | 按各源权重分配名额 | IT之家 3 / 中新网 3 / 爱范儿 2 / Solidot 2 / 少数派 1 / 极客公园 1 |
| `latest` | 全部按发布时间倒序，谁新谁上 | IT之家 8 / 中新网 4 |

`latest` 会让更新快的源挤满候选位，适合「就要最新的」场景；想要视野均衡用 `diverse`。

抓取侧：各源并发拉取，最多 6 路。单个源超时或解析失败只记错误，不影响其他源。

> 注意：地址校验是**整批先做**的。列表里只要有一个地址不合法（`http`、内网 IP、本地主机名），整批直接报错，不会跳过它继续抓其他源。

### 命令

```text
新闻说说 获取 [数量] [中国/国际/IT之家/混合]   拉取候选新闻，按发布时间排序并缓存
新闻说说 预览 [序号/中国/国际/IT之家/混合]     让 LLM 写成原创说说，只预览不发布
新闻说说 发布 <序号>                            从缓存里选一条，生成原创说说并立即发布
```

兼容别名：`获取新闻`、`新闻列表`、`新闻说说预览`、`发布新闻说说`。

命令里显式写了范围（如 `新闻说说 获取 3 IT之家`）就以命令为准；不写才用配置里勾选的源。

### 相关配置

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `news.sources` | 空 | 勾选的新闻源，优先于 `news.scopes` |
| `news.mix_mode` | `diverse` | `diverse` / `balanced` / `weighted` / `latest` |
| `news.scopes` | `["tech","news"]` | `news.sources` 为空时生效的分组 |
| `news.custom_rss_urls` | 空 | 额外的自定义 `https` RSS 地址 |
| `news.keywords` | 空 | 额外的搜索关键词，每个会请求一次 Google News 搜索源（仅对谷歌生效） |
| `news.max_candidates` | `12` | 默认交给 LLM 的候选数量，命令里可临时覆盖 |
| `news.recency_hours` | `36` | 只用这个时间范围内的新闻；`0` 表示不按时间过滤 |
| `news.max_post_length` | `180` | 生成的新闻说说截断长度 |
| `news.once_per_day` | `true` | 定时任务每天最多成功发布一次；手动发布不受限 |
| `news.trust_env` | `true` | 新闻 RSS 走系统代理。只影响新闻请求，不影响 QQ 空间 Cookie |
| `trigger.news_cron` | 空 | 新闻自动发说说的 cron，格式 `分 时 日 月 周`，也支持两段式 `分 时` |
| `trigger.news_offset` | `0` | 在基准时间之后的随机延迟秒数（0–3600） |
| `llm.news_provider_id` | 空 | 新闻说说用的 LLM，留空时回退写说说 provider，再回退当前会话默认 |

---

## 📦 安装

**方法一：通过 AstrBot 插件市场（推荐）**

1. 打开 AstrBot WebUI
2. 进入「插件管理」
3. 搜索「QQ空间Ultra CE」（作者 `DFSMHFJ`）
4. 点击「安装」

**方法二：手动安装**

在 AstrBot 插件目录下克隆仓库：

```bash
cd AstrBot/data/plugins          # Docker 部署是 /AstrBot/data/plugins
git clone https://github.com/DFSMHFJ/astrbot_plugin_qzone_ultra_ce.git
```

然后在 WebUI「插件管理」里重载插件列表。目录名必须保持 `astrbot_plugin_qzone_ultra_ce`，才能和 `metadata.yaml` 对上。

依赖不用手动装——AstrBot 加载插件时会读插件根目录的 `requirements.txt` 自动安装。

装好后在 QQ 私聊或群聊发送 `/qzone status` 检查 daemon 和 Cookie 状态。

插件首次使用时按需启动本地 daemon，默认 `18999` 端口、只监听 `127.0.0.1`，用来隔离 QQ 空间请求、Cookie 管理和渲染。除非端口被占用不建议改；被防火墙拦了就放行 `18999`。

浏览器打开 `http://127.0.0.1:18999/` 或 `/health` 只返回 `daemon_state`、`daemon_port`、`daemon_version` 三个字段，用来确认端口可达。完整状态、Cookie、QQ 号必须走插件命令或带 `X-Qzone-Secret` 的本地请求。

## 其余功能

新闻以外，本版与原版一致：看 / 读 / 评 / 赞 / 发 / 删说说、评论回复、表白墙投稿与审核、定时自动发说说、自动评论好友新动态、LLM tools、本地 daemon、说说卡片渲染、可选 pillowmd 风格。

定时发说说还能接 Life Scheduler 日程 + OmniDraw 自拍生图，把当天行程变成一条带图的日常说说——由 `life_publish.*` 一组开关控制，详见配置面板的说明。

发布效果示例：

<p align="center">
  <img src="docs/assets/life-selfie-qzone-demo.jpg" alt="插件发布到 QQ 空间的说说效果" width="520">
</p>

完整功能说明见[上游仓库](https://github.com/diaomin66/astrbot_plugin_qzone_ultra)。

## 与原版的差别

功能上动了多源 RSS 新闻这一块，外加一处 daemon 并发修复，共涉及 6 个文件：

- `qzone_bridge/news.py` — 整体重写为源注册表 + 多源并发抓取 + 四种混合策略 + 跨源去重
- `qzone_bridge/settings.py` — 新增 `news.sources`、`news.mix_mode`；`news.scopes` 默认值改为 `["tech","news"]`
- `main.py` — 走 `mix_news_items()`，来源标签用 `source_label()`，命令帮助补新源名
- `_conf_schema.json` — 新增对应配置项
- `qzone_bridge/daemon.py` — 视频发布链路里四处同步阻塞调用（ffprobe 探时长、ffmpeg 抽封面 ×2、远程视频流式下载）改走 `asyncio.to_thread`，原先一次远程视频发布会把 daemon 事件循环卡住几十秒
- `qzone_bridge/__init__.py` — `__version__` 跟着插件版本走，否则更新插件后旧 daemon 不会自动重启，新代码不生效

除此之外还改了插件标识（`metadata.yaml`）、CI 与上游监视工作流、发布自检脚本、回归测试、行尾统一（`.gitattributes`），以及把演示图从 PNG 换成 JPG 压体积。**`qzone_bridge/` 下其余模块、`pages/`、`daemon_main.py` 与上游逐字节一致。**

逐条改动见 [CHANGELOG.md](CHANGELOG.md)。

## 从上游迁移

本版标识符是 `astrbot_plugin_qzone_ultra_ce`，配置和数据都挂在新名字下，从原版切过来必须搬一次，否则等于全新安装。

**顺序很重要**：先在面板停用原版，再动文件，最后启用本版。原版还在跑的时候复制，会把它正在写的 cron 状态和 daemon 数据一起拷成脏的。

```bash
cd <AstrBot 数据目录>

# 0. 先备份
cp -r config/astrbot_plugin_qzone_ultra_config.json /tmp/
cp -r plugin_data/astrbot_plugin_qzone_ultra /tmp/

# 1. 面板 → 插件管理 → 停用 astrbot_plugin_qzone_ultra

# 2. 搬配置和数据
cp config/astrbot_plugin_qzone_ultra_config.json \
   config/astrbot_plugin_qzone_ultra_ce_config.json
cp -r plugin_data/astrbot_plugin_qzone_ultra \
      plugin_data/astrbot_plugin_qzone_ultra_ce

# 3. 面板 → 启用 QQ空间Ultra CE
# 4. 确认无误后卸载原版，避免下次误启用
```

老版本如果把数据放在插件目录内的 `data/qzone` 而不是 `plugin_data/`，也要一并复制到新插件目录的同名位置。

迁移完发 `/qzone status`，Cookie、daemon 状态、绑定的 QQ 号都应该还在；再发 `/获取新闻` 确认新闻源配置没丢。

## Cookie 绑定

推荐在 OneBot v11 环境用自动绑定。AstrBot 内置适配器名称仍叫 `aiocqhttp`，但插件按通用 OneBot 协议处理，不限定具体实现——LLOneBot、LLBot、NapCat、Shamrock 等都走同一套解析和兜底逻辑。

```text
/qzone autobind
```

插件安装、重载、AstrBot 启动时会各尝试一次 autobind；OneBot 客户端稍后才就绪的，会在首次捕获事件后补触发。最多 3 次，仍失败就手动绑：

```text
/qzone bind p_skey=...; p_uin=o123456789; uin=o123456789; skey=...
```

也可以在插件配置里填 `cookies_str`，初始化时自动写入登录态。

## 中文命令

序号从 `1` 开始，`1` 或 `最新` 表示最新一条，`-1` 表示当前页最后一条，支持范围 `1~3`。旧用法里的 `0` 兼容为最新一条。

| 命令 | 别名 | 权限 | 用法 | 说明 |
| --- | --- | --- | --- | --- |
| 查看访客 | - | 管理员 | `查看访客` | 查看最近访客 |
| 看说说 | 查看说说 | 管理员 | `看说说 [@用户/QQ] [序号/范围]` | 查看好友动态或指定用户说说；范围结果会合成一张长图返回 |
| 读说说 | - | 管理员 | `读说说 [@用户/QQ] [序号/范围]` | 只读取并返回渲染卡片，不评论不点赞 |
| 评说说 | 评论说说 | 管理员 | `评说说 [@用户/QQ] [序号/范围] [评论内容]` | 评论并返回原文与评论分区卡片；内容为空时由 AI 生成 |
| 赞说说 | - | 管理员 | `赞说说 [@用户/QQ] [序号/范围]` | 点赞说说 |
| 发说说 | - | 管理员 | `发说说 <文本> [图片/视频]` | 立即发布；单视频优先走 daemon 原生视频后台直发 |
| 发日常说说 | - | 管理员 | `发日常说说` | 立即执行日程 + 自拍 + 发布完整链路 |
| 写说说 | 写稿 | 管理员 | `写说说 <主题> [图片/视频]` | 生成待审核或待发布文案 |
| 新闻说说 获取 | 获取新闻、新闻列表 | 管理员 | `新闻说说 获取 [数量] [中国/国际/IT之家/混合]` | 拉取候选新闻并缓存 |
| 新闻说说 预览 | 新闻说说预览 | 管理员 | `新闻说说 预览 [序号/中国/国际/IT之家/混合]` | 预览 LLM 生成的原创说说，不发布 |
| 新闻说说 发布 | 发布新闻说说 | 管理员 | `新闻说说 发布 <序号>` | 选择缓存候选，生成并立即发布 |
| 删说说 | - | 管理员 | `删说说 <序号>` | 删除自己发布的说说 |
| 回评 | 回复评论 | 管理员 | `回评 <稿件ID> [评论序号]` | 回复已缓存稿件或已发布说说下的评论 |
| 投稿 | - | 所有人 | `投稿 <文本> [图片/视频]` | 投稿到表白墙 |
| 匿名投稿 | - | 所有人 | `匿名投稿 <文本> [图片/视频]` | 匿名投稿 |
| 撤稿 | - | 所有人 | `撤稿 <稿件ID>` | 撤回自己的待审核投稿 |
| 看稿 | 查看稿件 | 管理员 | `看稿 [稿件ID]` | 查看待审核稿件 |
| 过稿 | 通过稿件、通过投稿 | 管理员 | `过稿 <稿件ID>` | 审核并发布稿件 |
| 拒稿 | 拒绝稿件、拒绝投稿 | 管理员 | `拒稿 <稿件ID> [原因]` | 拒绝稿件 |

保留的兼容命令：

```text
/qzone help
/qzone status
/qzone bind <cookie>
/qzone autobind
/qzone videoauth <login_data_b64> [login_key_b64] [token_type] [token_appid] [token_wt_appid]
/qzone autovideoauth
/qzone unbind
/qzone feed [hostuin] [limit] [cursor]
/qzone detail <hostuin> <fid> [appid]
/qzone post <content>
/qzone comment <hostuin> <fid> <content>
/qzone like <hostuin> <fid> [appid] [unlike]
```

会读写已绑定 QQ 空间状态的兼容命令只允许管理员；普通用户仍可用投稿、匿名投稿和撤稿。

## LLM 工具

推荐：`llm_view_feed`、`llm_publish_feed`

兼容：`qzone_get_status`、`qzone_list_feed`、`qzone_view_post`、`qzone_detail_feed`、`qzone_publish_post`、`qzone_comment_post`、`qzone_delete_post`、`qzone_like_post`

`selector` 支持 `latest`、`最新`、`第2条`、`2`、`1~3` 或真实 `fid`；旧参数 `hostuin`、`fid`、`appid`、`latest`、`index` 仍兼容。会读写账号状态的工具默认只允许管理员触发，避免群成员借用插件 Cookie 操作账号空间。

## 配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `admin_uins` | 空 | 管理员 QQ 号，多个用英文逗号分隔 |
| `cookies_str` | 空 | 可选 Cookie 字符串，用于初始化自动绑定 |
| `daemon_port` | `18999` | 本地 daemon 端口；不建议修改，需放行防火墙 |
| `auto_start_daemon` | `true` | 首次使用时自动启动 daemon |
| `auto_bind_cookie` | `true` | 登录态缺失时尝试从 OneBot 自动获取 Cookie |
| `manage_group` | 空 | 投稿审核通知群；为空时尝试私发管理员 |
| `pillowmd_style_dir` | 空 | 可选 pillowmd 样式目录 |
| `native_video_publish` | `true` | 单个本地视频优先交给 daemon 原生视频直发；关闭后拒绝视频发布 |
| `render_publish_result` | `true` | 发布成功后返回渲染图 |
| `render_feed_card_limit` | `5` | 看/读/评/赞说说时单次最多渲染的卡片数，多条合成一张长图 |
| `llm.post_provider_id` | 空 | 写说说用的 LLM；空表示当前会话默认 |
| `llm.comment_provider_id` | 空 | 评论用的 LLM |
| `llm.reply_provider_id` | 空 | 回评用的 LLM |
| `trigger.publish_cron` | 空 | 自动发说说 cron；空表示关闭 |
| `trigger.comment_cron` | 空 | 自动评论 cron；空表示关闭 |
| `trigger.publish_offset` | `0` | 自动发说说的随机延迟秒数（0–3600） |
| `trigger.comment_offset` | `0` | 自动评论的随机延迟秒数（0–3600） |
| `trigger.comment_latest_count` | `1` | 每次定时自动评论处理几条最新动态 |
| `trigger.like_when_comment` | `false` | 评论成功后是否顺手点赞 |
| `trigger.read_prob` | `0.0` | 收到消息时概率触发读说说和自动评论；`0` 关闭 |
| `trigger.send_admin` | `false` | 定时任务后向管理群或管理员私发结果和渲染图 |

新闻相关配置见上面「相关配置」一节。完整配置（含 `life_publish.*`）见 `_conf_schema.json`。

Cron 格式为 `分 时 日 月 周`，也支持两段式 `分 时`，例如 `30 8 * * *` 与 `30 8` 都表示每天 8:30。

## 排障

- **`/qzone status` 显示未绑定**：先 `/qzone autobind`，失败后用 `/qzone bind <cookie>`。
- **daemon 起不来**：确认 `18999` 没被占用、防火墙已放行，再看 AstrBot 日志。
- **升级后 daemon 还是旧版本**：`/qzone status` 里的 `daemon_version` 应该等于插件版本。插件检测到不一致会自动重启 daemon；如果还停在旧值，手动重启一次 AstrBot。
- **自动绑定失败**：确认协议端是 OneBot v11，且适配器允许获取 Cookie。
- **新闻拉不到**：先确认 `news.trust_env` 开着（谷歌源需要代理）；国内源直连即可。单个源挂了不影响其他源，看日志里具体是哪个 URL。
- **`新闻说说 预览` / `发布` 报「余额或配额不足」**：LLM 供应商账户欠费（402）。充钱，或者在插件配置里把 `llm.news_provider_id` 指到有余额的 provider——这项留空会回落到当前会话默认 provider，也就是欠费那个。
- **报错看不懂**：`新闻说说 预览` / `发布` 失败时会分成余额不足、限流、鉴权失败、请求被拒、服务端故障、超时网络六类，并带上 provider 名和原始错误。按回复里的「建议」那行处理即可。
- **新闻候选太少**：`diverse` 会过滤同事件重复，宁可少几条也不让一件事占满候选位；换成 `balanced` 或调大 `news.max_candidates`。
- **图片/视频发布失败**：确认媒体文件能被 AstrBot 读到；封面提取依赖系统 `ffmpeg` 或 `imageio-ffmpeg`。
- **LLM 生成为空**：检查会话 provider，或单独配 `llm.post_provider_id`、`llm.comment_provider_id`。
- **点赞成功但提示校验不确定**：QQ 空间读回延迟，稍后再看。

## 开发与测试

```bash
pip install -r requirements.txt pyyaml

python -m compileall -q main.py daemon_main.py qzone_bridge   # 语法检查
python tests/test_news.py                                     # 58 项离线回归，不联网
python scripts/check_metadata.py                              # 发布前自检（字段 / 版本号 / 体积）
```

三个 workflow：`ci.yml` 在 push 与 PR 时跑上面三步；`upstream-watch.yml` 每天比对上游 `main` 的 SHA，有新提交就开一个 `upstream-sync` issue，**不自动合并**（`news.py` 已整体重写，上游一动新闻模块必冲突）。
