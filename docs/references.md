# 开源参考与实现取舍

参考资料于2026-10-27检索，交付整理于2026-10-28完成。参考公开格式与思路，独立实现，没有新增这些项目的运行依赖。

## ChatGPT Archive Tools

[仓库与README](https://github.com/jamesmoore/chatgpt-archive-tools) 展示了官方导出的本地浏览、逐对话 Markdown/HTML 和媒体资产整理，也区分阅读时的分支选择。Memory Bridge 借鉴原始归档与阅读视图并存的组织方式；本次新增 readable/、文件清单与独立成员副本。

## ChatGPT to Open WebUI

查看了[仓库](https://github.com/scubanarc/chatgpt-to-open-webui)及[convert.py](https://github.com/scubanarc/chatgpt-to-open-webui/blob/main/convert.py)。其转换流程提供了参考，但所读代码按时间将消息重新串成一条父子链，并使用固定默认模型；不适合本项目的完整分支目标。Memory Bridge 根据原始父子关系构建树，使用实际存在的模型元数据，缺失时间不会填成当前时间。

## Open WebUI 官方导入文档

[公开格式](https://docs.openwebui.com/features/chat-conversations/data-controls/import-export/) 定义了 chat 包装、history.messages、parentId、childrenIds 和 currentId。本项目按该结构生成分片，目标支持不了的角色、结构化载荷和历史版本仍留在通用归档及阅读文件里。平台导入可能生成新聊天，因此本工具内的幂等导入不意味着在目标平台重复导入也会去重。

目前只验证生成的数据结构和分支关系，尚未在运行中的 Open WebUI 测试。以上项目的功能描述不保证所有导出都含完整附件或账号内部状态，实际覆盖以用户输入的字节和本工具报告为准。
