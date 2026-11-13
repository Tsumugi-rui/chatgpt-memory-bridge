# ChatGPT Memory Bridge — MVP 数据模型与阶段设计

> 下文保留初版设计记录。v0.2 的当前模式见 docs/personal-mode.md：敏感候选不默认过滤、全量导出所有记忆状态，个人导入不设固定容量上限，新增 fidelity.py（全成员、附件引用、SQLite快照与恢复）和 portable.py（阅读视图、分片与平台投影）。数据库模型继续兼容 schema_version=1；members 是旧索引，完整条目清单由归档重新生成到 archive_members.jsonl。

## 约束

原始 ZIP 字节是权威来源；JSON、全文索引和记忆均为派生数据。只读取用户主动提供的文件，不登录账号、不联网、不自动运行附件或聊天中的指令。迁移包用于人工上传与上下文重建，不实现原生聊天列表、账号设置或官方长期记忆恢复。

## 目录

```
src/memory_bridge/
  archive.py       ZIP 检查、内容寻址归档、分块校验
  adapters.py      流式 JSON 识别与 ChatGPT 规范化
  store.py         SQLite 模式、事务、FTS5、增量索引
  memories.py      保守规则、审核、证据、版本与回滚
  export.py        迁移包、完整性报告、manifest 验证
  crypto.py        可选流式 AES-GCM 加密备份
  cli.py          argparse 命令
tests/            仅合成数据
data/             默认本地库；不提交 Git
  bridge.sqlite3
  archives/<sha256>.zip
  tmp/            应用拥有的临时文件
```

## 数据模型（SQLite schema_version=1）

- imports：ZIP 哈希唯一、状态、统计和警告；重复导入幂等。
- members：每个归档成员的原路径、大小、SHA-256、类型；从不按不可信路径解压。
- conversations：平台+来源命名空间+对话 ID 唯一，保存当前索引、未知元数据。
- conversation_versions：完整原始对话 JSON 的内容版本；每个版本有导入与归档成员定位。
- messages：对话内节点 ID 唯一，保留消息 ID、角色、时间、父子、正文与原始节点。
- message_versions：消息的所有内容版本。
- observations：某次导入观察到的对话和消息版本；缺失只做统计，不删除历史。
- messages_fts：FTS5 trigram 支持中日韩子串；短词退回 LIKE。
- memories：内容、类型、事实/偏好/推断、状态、确认、置信度、冲突键。
- memory_sources：记忆到具体消息版本的证据，不随后续消息修改漂移。
- memory_versions：每次人工或自动变更的完整快照，可回滚。

同账号多次导出应使用同一 `--source`，不同账号使用不同命名空间，防止相同 ID 错配。

## 格式兼容与故障策略

兼容根数组、conversations/items/data.conversations 包装数组、单对话对象和 ID 为键的对象；mapping 分支与 messages 数组分别测试。缺失 ID 使用内容哈希派生 ID，并明确告警：无法保证缺失 ID 的变更匹配。保留原节点与完整对话，未知字段不丢失。单个对话在内存中构建；整个文件不一次加载。损坏 JSON 保留此前成功解析的对话，标记 partial；拒绝的 ZIP 仍保留输入字节供人工恢复。

## 安全

ZIP 拒绝绝对路径、..、盘符、反斜杠路径、符号链接、Windows 保留名、重复成员（大小写归一）、加密成员和超限压缩比/大小/成员数。实际读取再次计数并验证 CRC；失败时记录不完整。只保存内容寻址的 ZIP，不解压成员。上限可由库 API 配置，默认单成员 2 GiB、总展开 8 GiB、ZIP 8 GiB、10 万成员、压缩比 1000。

记忆规则只接受用户角色的明确第一人称陈述，默认只识别偏好、兴趣、学习、项目、目标和待办；不把 AI 文本作为用户事实。候选默认未确认，敏感内容与疑似注入整条跳过。规则不能全面识别敏感信息或自然语言矛盾；人工审核是必要环节。确认且有效的记忆才进入初始化 Markdown。默认 memory.json 也只含确认有效项，可显式包含审核队列。未实现 LLM 调用；预留 Protocol，不存在隐式外发路径。

记忆删除为需确认的软删除，可回滚；不会删除原始备份。加密备份用可选 cryptography、scrypt 派生密钥、随机 salt/nonce、流式 AES-256-GCM。密码交互输入，不写配置或日志。普通库和迁移包仍是明文。

## 交付阶段

1. 环境、目录、数据模型与兼容性契约。
2. 安全 ZIP 归档、清单与原文件校验。
3. 流式解析、SQLite/FTS5、不可变版本与增量统计。
4. 离线规则、人工审核、冲突、历史、回滚与去重合并。
5. 可验证迁移目录、独立原始 ZIP 备份、提示词、分层完整性报告。
6. 合成测试与 CLI 端到端演示；故障与篡改测试。
7. UI 和在线/本地 LLM 后续实现，不作为本轮 MVP 前提。
