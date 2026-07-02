# SyncRemover MoviePilot 插件设计

## 背景

需要开发一个 MoviePilot V2 插件，在 MoviePilot 删除媒体相关记录或文件时，同步删除 qBittorrent、Transmission 下载任务，并按配置清理硬链接媒体文件。插件默认删除下载器原始下载数据，硬链接清理范围可配置，默认只处理当前文件以降低误删风险。

## 目标

- 监听 MoviePilot 删除相关事件，自动触发同步清理。
- 支持 qBittorrent 和 Transmission。
- 提供“是否删除原始下载数据”配置，默认开启。
- 提供硬链接清理范围配置：仅当前文件或同任务全部媒体硬链接，默认仅当前文件。
- 下载器删除失败时默认不继续删除硬链接，除非显式开启高级开关。
- 记录每次执行的审计信息，支持失败重试和 dry-run 预览。

## 非目标

- 初版不做 Vue 模块联邦前端，只使用 MoviePilot V2 Vuetify JSON 配置页和详情页。
- 初版不支持只靠标题自动删除任务，避免同名资源误删。
- 初版不跨全盘扫描硬链接，只在配置的媒体目录和下载目录白名单内处理。
- 初版不接管 MoviePilot 系统删除模块，避免与宿主版本和其它插件产生高耦合。

## 插件结构

```text
plugins.v2/syncremover/
├── __init__.py
├── README.md
└── requirements.txt
```

主类为 `SyncRemover`，继承 MoviePilot 的 `_PluginBase`。插件 ID 和目录名保持对应，面向 V2 插件市场放入 `plugins.v2/syncremover/`。若宿主已提供下载器封装，优先复用宿主能力；只有缺少必要客户端时才在 `requirements.txt` 增加额外依赖。

## 事件入口

插件监听以下事件：

- `HistoryDeleted`：用户删除转移或下载历史时触发。
- `DownloadFileDeleted`：用户删除下载源文件时触发。
- `DownloadDeleted`：用户删除下载任务时触发。

所有事件统一进入 `handle_delete_event(event)`，由 `DeleteContextParser` 将 `event.event_data` 归一化为 `DeleteContext`。由于 MoviePilot 事件数据是字典且缺少强类型保证，解析器必须兼容字段缺失、字段改名、路径列表和单路径等情况。

```text
DeleteContext
- event_type
- media_paths
- download_path
- downloader
- torrent_hash
- torrent_id
- title
- source
- confidence
```

`confidence` 用于标识匹配可信度。只有 `task_id`、`hash`、完整路径、历史记录反查等强证据可以自动删除；仅标题匹配时写入待人工确认。

## 任务匹配

`TaskMatcher` 按强证据优先执行：

1. 如果事件 payload 直接带 qBittorrent `hash` 或 Transmission `id/hashString`，直接定位任务。
2. 如果事件带下载目录或源文件路径，扫描下载器任务和文件列表，完整路径命中才算匹配。
3. 如果事件只带媒体库路径，先查 MoviePilot 转移历史或插件审计记录，反查原始下载路径，再按路径匹配下载器任务。
4. 如果只有标题或种子名，默认不自动删除，记录为待人工确认。

下载器适配层提供统一接口：

```text
DownloaderAdapter
- list_torrents()
- list_files(task_ref)
- delete_task(task_ref, delete_source_data)
```

具体实现拆为 `QbittorrentClient` 和 `TransmissionClient`。插件主流程不直接散落调用下载器 API，避免后续扩展多下载器或多实例时重写业务逻辑。

## 硬链接清理

`HardlinkResolver` 使用文件系统元数据判断硬链接关系：

```text
same device + same inode + link count > 1
```

清理策略：

- `current_file`：仅处理事件中当前媒体文件对应的下载源文件或任务数据，不额外扫描同任务其它媒体文件。
- `whole_task_media`：根据下载任务文件列表，在媒体目录白名单内查找同 inode 文件，并删除同任务相关媒体硬链接。

默认策略为 `current_file`。`whole_task_media` 适合整季、合集清理，但误删范围更大，必须依赖媒体目录白名单。

## 删除执行顺序

`DeleteExecutor` 的流程：

1. 构造 `DeleteContext`。
2. 定位下载器任务。
3. 检查路径白名单、匹配可信度、任务状态和 dry-run。
4. 如果允许删除，调用下载器删除任务；`delete_source_data=true` 时同步删除下载器原始数据。
5. 下载器删除成功后，按硬链接策略删除媒体侧硬链接。
6. 写入审计记录，包括事件类型、匹配依据、删除动作、结果和失败原因。

下载器删除失败时默认不继续清理硬链接。高级配置 `continue_hardlink_on_downloader_failure` 可以覆盖该行为，但默认关闭。

## 配置

```text
enabled: false
delete_source_data: true
hardlink_scope: current_file
enabled_downloaders:
  - qbittorrent
  - transmission
media_dirs: []
download_dirs: []
strict_path_guard: true
continue_hardlink_on_downloader_failure: false
dry_run: false
audit_limit: 200
```

关键默认值：

- `delete_source_data=true`：默认删除下载器原始下载数据。
- `hardlink_scope=current_file`：默认只处理当前文件。
- `strict_path_guard=true`：任何实际删除必须命中媒体目录或下载目录白名单。
- `continue_hardlink_on_downloader_failure=false`：下载器删除失败时不继续文件清理。
- `dry_run=false`：正常执行；打开后只记录预期动作。

## 页面和 API

配置页使用 Vuetify JSON：

- 启用插件。
- 演练模式。
- 删除原始下载数据。
- 硬链接清理范围。
- qBittorrent / Transmission 启用开关。
- 媒体目录白名单。
- 下载目录白名单。
- 下载器删除失败仍继续清理硬链接。

详情页显示：

- 最近执行记录。
- 成功、失败、跳过、待确认计数。
- 待人工确认记录。
- 重试按钮。
- 清空审计按钮。

插件 API：

```text
GET  /audit
POST /retry
POST /dry-run
POST /clear-audit
```

`POST /dry-run` 接收路径或审计记录 ID，返回将匹配的下载任务和将删除的路径，但不执行删除。

## 审计和幂等

`AuditStore` 保存最近 `audit_limit` 条记录，状态包括：

- `success`
- `failed`
- `skipped`
- `pending_confirm`
- `dry_run`

文件不存在视为幂等跳过，不作为致命错误。重复事件命中已删除任务或已删除文件时，记录为 `skipped` 并保留原因。

## 错误处理

- 缺少路径白名单：拒绝实际删除，记录失败。
- 仅标题匹配：不自动删除，记录待人工确认。
- 下载器连接失败：停止本次清理，记录失败。
- 下载器任务不存在：若文件也不存在则跳过；若仍有硬链接候选，要求人工确认或 dry-run 后重试。
- 路径不在白名单内：拒绝删除。
- 硬链接扫描异常：不影响已完成的下载器删除，但记录部分失败。

## 测试策略

单元测试：

- `DeleteContextParser` 兼容不同 event_data 结构。
- `TaskMatcher` 按 hash、id、完整路径、历史反查、标题降级匹配。
- `HardlinkResolver` 只在 same device 和 same inode 时识别硬链接。
- `DeleteExecutor` 验证删除顺序、下载器失败中断、dry-run、不在白名单拒绝删除。

集成测试：

- mock qBittorrent 和 Transmission 客户端。
- 构造临时目录和真实硬链接，验证 `current_file` 与 `whole_task_media` 行为。
- 验证重复事件不会报错，并写出幂等审计记录。

手工验证：

- 在 MoviePilot 测试环境安装插件。
- 打开 dry-run，删除一个测试媒体文件，确认匹配任务和删除计划正确。
- 关闭 dry-run，用测试下载任务验证 qb/tr 删除任务和源数据。
- 验证下载器删除失败时不会继续删除硬链接。

## 实现风险

- MoviePilot 删除事件 payload 可能随版本变化。解析器必须集中处理兼容，不能在主流程硬编码字段。
- 不同下载器客户端的删除参数语义不同。适配层必须统一成 `delete_source_data`，并在日志里记录实际调用参数。
- Docker 容器路径映射可能导致 MoviePilot、下载器、宿主文件路径不一致。实现时需要提供路径映射配置或复用 MoviePilot 现有路径映射能力。
- 硬链接跨文件系统不存在，但路径映射可能造成同一文件在不同容器路径不可直接 stat。实现前需要在目标部署环境验证路径可见性。

## 参考

- MoviePilot V2 插件开发指南：`https://github.com/jxxghp/MoviePilot-Plugins/blob/main/docs/V2_Plugin_Development.md`
- MoviePilot 事件枚举：`app/schemas/types.py`
- MoviePilot 事件管理器：`app/core/event.py`
