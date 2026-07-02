# 同步删除助手

MoviePilot V2 插件：同步删除 qBittorrent、Transmission 下载任务，并清理对应硬链接媒体文件。

## 安装地址

在 MoviePilot 的插件市场或第三方插件订阅中添加：

```text
https://raw.githubusercontent.com/jeck5001/syncremover-plugin/main/package.v2.json
```

## 主要能力

- 支持 qBittorrent 和 Transmission。
- 删除下载器任务时默认同时删除原始下载数据。
- 支持按下载源路径或媒体硬链接路径手动执行一次。
- 支持演练模式，先看匹配和删除计划，不实际删除。
- 支持媒体目录、下载目录白名单，避免误删白名单外路径。
- 手动执行会把路径扫描根目录作为兜底安全根，避免已匹配任务后仍因未选择白名单被拦截。
- 支持从 `/vol2/1000/media`、`/media`、`/downloads`、`/mnt`、`/data`、`/volume1` 扫描候选目录。

## 推荐配置

先打开演练模式，再配置白名单。

媒体目录白名单示例：

```text
/vol2/1000/media/movie
/vol2/1000/media/tv
/vol2/1000/media/cartoon
```

下载目录白名单示例：

```text
/vol2/1000/media/download
/vol2/1000/media/incomplete
```

如果候选目录没有扫出来，就在手填目录里按“每行一个”输入 MoviePilot 容器内路径。

## 手动执行一次

1. 打开“演练模式”。
2. 在“手动执行目标路径”填一个文件路径。
3. 打开“立即执行一次”。
4. 保存配置。
5. 打开插件日志查看结果。

目标路径可以是下载源文件：

```text
/vol2/1000/media/download/xxx.mkv
```

也可以是已经入库的媒体硬链接文件：

```text
/vol2/1000/media/cartoon/xxx.mp4
```

## 日志判断

保存后应看到类似日志：

```text
同步删除助手：立即执行开始，目标路径：...
同步删除助手：立即执行完成，状态：dry_run/success/skipped/failed，原因：...，下载器：...，任务：...，硬链接：N，路径：...
```

状态含义：

- `dry_run`：演练命中，不会真实删除。
- `success`：真实删除完成。
- `skipped`：没有匹配到下载器任务。
- `failed`：被白名单、路径守卫或下载器删除结果拦截。

如果填的是媒体硬链接路径，匹配成功时原因会显示 `hardlink_path`。
`硬链接：N` 表示本次额外删除的媒体硬链接数量。
如果被路径守卫拦截，日志原因会显示当前允许根目录 `allowed roots=...`，把目标路径所在目录加入扫描根目录、媒体目录白名单或下载目录白名单即可。

## 安全边界

- 插件默认关闭，需要手动启用。
- 删除原始下载数据默认开启，可在配置里关闭。
- 严格路径守卫默认开启，只处理媒体目录和下载目录白名单内路径。
- 仅标题匹配不会自动删除。
- 插件不会全盘扫描硬链接。

## 插件目录

MoviePilot 插件源码位于：

```text
plugins.v2/syncremover
```
