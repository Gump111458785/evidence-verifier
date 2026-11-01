# Evidence Verifier Chrome sidecar 0.2.0

解压Release插件ZIP，在Chrome扩展管理页手动加载manifest.json所在目录。Chrome 116+。本项目不会自动安装或变更权限。

默认打开真实对话侧栏sidecar.html，教学案例另在plugin.html。主动读取ChatGPT、Claude、Gemini可见回答、引用与工具片段；选择器适配未完成三站点实机验证，失效时手动粘贴。不能提取模型隐藏思维链或完整内部检索历史。

- activeTab / scripting：在用户主动授权的活动页读取可见DOM、跟随更新或填入草稿。
- sidePanel / contextMenus：打开侧栏及保留原主动选中文本入口。
- storage：仅扩展会话保存快照与任务ID，未自动读取整段聊天历史。
- 本机127.0.0.1权限与CSP：只连接8790/18790服务，不自动发送到站点后台。

真实核验须配置本机服务及精确EV_EXTENSION_ID，点击提交才发送证据。服务可能调用用户配置的模型，请自行确认配置。原文由用户打开引用页后采集，不静默抓取远程链接；无模型、无证据不伪造结果。补充prompt可以复制或填入空草稿，不自动发送、不覆盖现有草稿。

完整步骤、上下文长度、额外工具预算、状态与隐私范围见仓库docs/conversation-sidecar.md。设计依据Chrome官方[activeTab](https://developer.chrome.com/docs/extensions/develop/concepts/activeTab)与[Scripting API](https://developer.chrome.com/docs/extensions/reference/api/scripting)。
