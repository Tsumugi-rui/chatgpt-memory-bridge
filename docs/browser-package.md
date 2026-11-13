# 浏览器采集包导入

自动识别 ZIP 根目录的 `manifest.json`、`对话全文.json`、`完整性报告.json`。已支持由本项目插件 1.0、1.1 生成的 ZIP；不是任意网页导出器的通用导入器。

插件 manifest 的条目为 `{path, bytes, sha256}`；逐条验证字节数、哈希、成员清单、重复成员与必需文件。未通过验证时只保存原 ZIP，不建立误导性正文索引。

`对话全文.json` 的 `segments` 是显示顺序的消息数组。每个消息的 ID、角色、正文、HTML、版本和附件引用保留在规范化节点的 `browser_message` 中。没有 ID 的消息根据位置及邻近来源生成派生标识，并产生警告；不会仅按短句内容去重。片段之间不建立虚假的连续父子边。

原始片段支持：

- v1：`job.snapshots[*].messages` 直接保存消息。
- v2：`format=memory-bridge-reference-v2`，消息集中存于 `dictionary`，`job.snapshots[*].messages` 为下标。导入验证所有引用范围，原始引用字典仍以原包完整保留，不为建索引重复展开全部 HTML。

`_memory_bridge_browser.node_order` 明确保存消息显示顺序，避免 SQLite 中规范化 JSON 的键排序改变对话时间线。采集时间与来源消息时间区分；没有原始消息时间时不杜撰。

已保存附件的字节必须与 manifest 及附件清单一致，通过本地 `asset_pointer` 关联；离线阅读文件引用 `files/` 下保留的内容。过期链接、按钮无直链和未下载资源继续标记，不视为取得。

导入状态：校验通过且页面首尾、衔接和附件齐全时可完成输入包解析；页面未完成、分段或附件缺失则为 `partial`。这两种状态都不能证明账号全量恢复。原始包哈希、范围和告警进入 `imports.json`、`browser_coverage.json` 和迁移完整性报告。

集成不调用 ChatGPT 内部接口，不上传私人对话。浏览器采集、软件导入和目标账号上下文重建是三个独立步骤，官方 ZIP 不再是必需输入。
