# ChatGPT Memory Bridge v0.2.0

个人使用的本地 Python CLI，默认以完整保留为优先。原始 ZIP 是权威备份，聊天索引、可读文件和结构化记忆都是可重建的派生资料。

## 快速使用

在项目根目录运行（Windows PowerShell，Python 3.11+）：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[test,crypto]"
$bridgePython = ".\.venv\Scripts\python.exe"
& $bridgePython -m memory_bridge import "D:\Backups\chatgpt-export.zip" --source old-account
& $bridgePython -m memory_bridge memory extract
& $bridgePython -m memory_bridge export --out .\outputs\my-migration
& $bridgePython -m memory_bridge verify .\outputs\my-migration
```

当前工作目录的虚拟环境已安装依赖，可直接从 import 开始。`--data` 默认为 `data/`；指定其他资料库时放在子命令前，如 `--data D:\MyArchive import ...`。全程不需要账号密码、Cookie 或 API Key，也不会自动上传文件。核心运行依赖仅 ijson；crypto 和 test 是可选依赖。

同账号每次导入保持相同 `--source`，不同账号使用不同值。重复 ZIP 不重复创建记录，消息修改会保留所有观察到的版本，新导出缺少的内容不删除。正常新文件按导入顺序更新当前索引，建议按导出日期从早到晚导入。

## v0.2 的个人全量默认值

- 导入不设置固定 ZIP 大小、压缩比或成员数上限；可读取内容尽量索引。
- 敏感信息不再默认过滤。规则提取遍历所有历史消息版本，不仅查看当前版本；不会因为消息很长而整条跳过。
- 全部记忆状态、证据和版本历史默认进入迁移包，包括未确认、冲突、过时、合并和软删除项。状态标签始终保留，不会自动把候选改成已确认事实。
- 所有可读取成员逐字节导出，包括附件、HTML、账号资料 JSON 和未知文件。使用归档哈希和成员序号命名，同名或特殊路径条目也分别保留，原路径在清单中。
- 导出一致的 SQLite 快照，保留数据库中的全部表、全文索引、导入观察记录、记忆证据和审核历史，可在新目录恢复并继续增量导入。
- 同时生成包含全部版本、全部分支和角色的可读文件，以及平台 JSON 分片。

为了完整性，仍校验哈希、CRC，并避免覆盖已有迁移目录。这些校验用于发现文件损坏和遗漏。成员读取失败不会使原始 ZIP 从包中消失，具体失败记录写入报告。

## 迁移包结构

| 路径 | 内容 |
|---|---|
| originals/*.zip | 原始输入字节的独立备份 |
| files/归档哈希/成员序号.扩展名 | 全部可读取文件的独立副本 |
| archive_members.jsonl | 每个成员的原路径、序号、哈希、ZIP 元数据与读取结果；包含重复条目 |
| account_data.json | 账号 JSON/HTML 等资料位置；标记是否含已索引对话，不猜测文件一定是官方记忆 |
| attachment_references.jsonl | 全部历史消息的附件引用，列出匹配、歧义和本地缺失 |
| conversations.jsonl | 规范化节点及所有已观察到的原始对话版本 |
| readable/index.json、readable/*.md | 逐对话索引和完整分支阅读文件 |
| memory.json、memory_history.jsonl | 全部记忆与修改历史 |
| profile.md、preferences.md、learning.md、projects.md、reflections.md | 按类型整理记忆，每条带状态和来源 |
| library/bridge.sqlite3 | 可恢复本工具状态的一致快照 |
| platforms/chatgpt/conversations-*.json | 当前对话原始对象的 JSON 数组分片，用作参考资料上传 |
| platforms/openwebui/conversations-*.json | 按 Open WebUI 公开格式生成的消息树 |
| portability_report.json | 平台转换中无法表示的节点、字段类别和超大单对话 |
| bootstrap_prompt.md、migration_report.md | 新账号初始化提示词与分项完整性报告 |
| imports.json、inventory.jsonl、manifest.json | 导入统计、旧版索引清单、全包大小和哈希 |

`archive_members.jsonl` 是完整成员清单；旧版 `inventory.jsonl` 按路径建索引，同名成员可能合并，不能替代前者。未识别的内容仍在 files/ 与原始 ZIP 中。

默认分片目标为20 MiB，`export --chunk-mb 10` 可调整。超大单个对话不会截断，会单独保留并在报告标明。文件大小目标不是目标平台上传能力的保证。

## 恢复整个本地资料库

```powershell
& $bridgePython -m memory_bridge restore .\outputs\my-migration --out .\restored-data
& $bridgePython -m memory_bridge --data .\restored-data verify
& $bridgePython -m memory_bridge --data .\restored-data search "数学"
& $bridgePython -m memory_bridge --data .\restored-data import "D:\Backups\next-export.zip" --source old-account
```

恢复目录必须尚不存在。restore 先校验包，再恢复 SQLite 与原始 ZIP；它恢复的是 **Memory Bridge 自身的完整资料库**。如果使用了 `export --no-originals`，该包不能执行完整 restore。

## 在新账号或其他平台使用

在新 ChatGPT 对话中上传有关记忆文档、memory.json 或 platforms/chatgpt 下的分片，再使用 bootstrap_prompt.md。附件可通过附件引用清单找到本地副本，再按任务重新上传。可读文档包含多条分支，不能把互斥分支当成一条连续对话。

Open WebUI 文件保留目标支持的 user/assistant 分支关系；system/tool 角色、非文本载荷、未知元数据及较早对话版本保留在通用归档里，并在转换报告说明。附件不会自动变成目标平台的有效文件对象。**尚未在运行中的 Open WebUI 实测导入。**

本工具没有执行 ChatGPT 原生侧边栏聊天、官方长期记忆、项目权限或账号设置的写回。完整备份、上下文重建和原生账号状态恢复是不同结果。

## 检索、记忆和增量

```powershell
& $bridgePython -m memory_bridge search "日本語"
& $bridgePython -m memory_bridge search --topic "学习" --after "2026-01-01T00:00:00+08:00"
& $bridgePython -m memory_bridge conversations --id "conversation-id" --out .\outputs\selected.jsonl
& $bridgePython -m memory_bridge memory list
& $bridgePython -m memory_bridge memory review "memory-id" --confirm
& $bridgePython -m memory_bridge memory edit "memory-id" --content "修正后的记忆"
& $bridgePython -m memory_bridge memory outdated "memory-id"
& $bridgePython -m memory_bridge memory conflict "id-1" "id-2"
& $bridgePython -m memory_bridge memory history "memory-id"
& $bridgePython -m memory_bridge memory rollback "memory-id" --version 2
& $bridgePython -m memory_bridge memory delete "memory-id" --yes
```

规则只识别部分明确陈述，如“我偏好：先给例子”“我的学习进度：线性代数”“我的项目「工具」：已完成原型”。规则不是 LLM，也不会完整提取全部长期理解；没被规则识别的正文仍全部保存。助手发言不被直接当作用户事实。冲突保留双方，内容修改撤销确认，软删除保留历史。回滚只作用于指定记忆，相关项可分别回滚。

查询支持中日韩子串、对话 ID、消息时间；`--topic` 当前是标题关键词，不是语义分类。缺失稳定 ID 时派生 ID，修改匹配可能不可靠。分支中的同名消息 ID 会在规范化索引里区分，原始对象仍保留。

升级后可对已导入文件使用 `import ZIP --source old-account --retry` 重新解析，补建新版索引。较早导入的重试不会覆盖后续导入已经更新的消息。

## 可选模式与维护

- `memory extract --exclude-sensitive`：可选过滤敏感候选；默认不过滤。
- `export --confirmed-only`：仅缩减上下文记忆视图；原始聊天、数据库快照仍全量保存。
- `import ZIP --strict`：可选启用旧版结构限制和容量上限；默认个人全量模式。
- `repair-archive ZIP --yes`：用哈希完全一致的独立原件修复本地归档。
- `clean-temp --yes`：只清理应用创建、超过24小时的临时普通文件。
- `encrypt FILE --out BACKUP.mbenc` / `decrypt BACKUP.mbenc --out FILE`：可选交互密码加密备份，不是默认流程。

所有导出与恢复均不覆盖现有目录。manifest 最后生成；没有有效 manifest 的失败输出不视作完成。返回码0为成功，2为错误或 partial/rejected 导入，130为中断。哈希校验验证字节一致性，不证明源导出包含账号全部数据。

## 测试、示例与现有限制

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python examples\demo.py --work .\work\new-demo --out .\outputs\new-demo
.\.venv\Scripts\python -m memory_bridge verify .\outputs\new-demo
```

56项合成测试通过，包括完整数据库逐表恢复比对、恢复后增量导入、特殊/重复文件名、附件匹配、分片不截断、旧消息证据和分支转换。未使用真实官方 ZIP，也没有做多 GiB 压力测试。单个对话及其所有版本导出时仍可能占用较多内存；陌生包装、HTML 正文和未知数据不一定能被索引。离线原始文件始终是后续重处理的来源。

UI、LLM 记忆提取和 Claude/DeepSeek 专用适配器尚未实现。源码在当前目录，发布包只包含代码、文档、测试与合成示例。

参考项目与实现取舍见 docs/references.md：借鉴 [ChatGPT Archive Tools](https://github.com/jamesmoore/chatgpt-archive-tools) 的归档阅读组织，使用 [Open WebUI 公开导入结构](https://docs.openwebui.com/features/chat-conversations/data-controls/import-export/) 构造平台投影。
