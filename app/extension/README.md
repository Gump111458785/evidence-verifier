# Trisight 三省 Chrome 插件

在 Chrome 扩展管理页打开“开发者模式”，选择“加载已解压的扩展程序”，指向包含 `manifest.json` 的目录。需要 Chrome 116+，使用受邀账号登录。

打开 DeepSeek（`chat.deepseek.com`）、ChatGPT、Claude 或 Gemini 对话页后，插件自动读取页面上可见的最新问题、回答和前 3 轮上文；点击工具栏图标打开侧栏即可审查，不需要手动读取。2026-11-02 在 ChatGPT 上完成过单案例验收；DeepSeek、Claude、Gemini 自动读取兼容尚未在实机网页端全量回归验证。

- 自动审查：运行纠错、补充、预测三种模式。
- 手动审查：第一次点击展开选择，第二次点击运行所选模式。
- 结果中的提示词和预测问题只填入原对话的空输入框，不自动发送，不覆盖已有草稿；对话变化后拒绝填入。

权限说明：

- `chat.deepseek.com`、`chatgpt.com`、`claude.ai`、`gemini.google.com` 站点权限与内容脚本：只读取可见 DOM 中的问答，过滤思维链与思考块，不发起网络请求。
- scripting：为安装前已打开的对话页补注入同一脚本。
- storage：会话内保存每个标签页的快照；登录令牌保存在本地。
- sidePanel：侧栏界面。
- 审查请求只发往 `https://evidence-verifier.edgeone.dev`，且只在点击审查时发送。

接口说明见 [Trisight 审阅接口](../../docs/trisight-assist.md)。
