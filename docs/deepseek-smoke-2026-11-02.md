# DeepSeek 真实 API 冒烟记录 · 2026-11-02

这是接入与数据流验收，使用项目自编教学输入，不是独立评测集，也不产生可写入简历的准确率指标。密钥、供应商响应ID、私人配置和运行数据库不发布。main包含本次接入改动；旧v0.3.2下载包不包含这些新增内容。

后续已完成[真实网页自动补搜单开发案例](web-grounding-validation-2026-11-02.md)；本页原始范围、结果与费用不回写。

## 配置

- 四个角色使用`deepseek-flash`，Chat Completions、JSON object模式、`reasoning_effort=none`，不发送seed，不自动重试。
- 原生搜索端点`https://api.deepseek.com/v1/tools/search`；本次仅取一个结果的正文，不将摘要当作正文。
- 使用纯API入口及本机桥接；不加载本地权重、不运行训练。临时隔离环境仅安装CPU端jsonschema依赖。

## 观察结果

| 检查 | 实际结果 |
|---|---|
| 鉴权、模型发现与JSON输出 | 通过，服务返回deepseek-flash |
| 关系规划首次调用 | 失败：完整引用`Who designed X?`却给end=14，正确值为15；严格回放将其归类为invalid_output |
| 关系规划修改提示后复测 | 单次通过；增加程序计算的完整问题范围，并明确结束位置为exclusive，保留严格拒绝规则 |
| 任职不能推出教育 | 两条初始证据，判定missing，符合预期 |
| 不同人物之间的实体错绑 | 两条初始证据，判定missing，符合预期 |
| 增加对应人物的教育证据 | 三条自编证据，判定entailed，符合预期 |
| 报告与补充提示词 | 三份核验均生成trace和supplemental_prompt，automatically_sent=false |
| DeepSeek原生网页搜索 | 一次真实请求，返回一个含正文及来源URL的结果 |

三组核验走真实`/api/verifications`和本地关系内核。补证后的正例使用事先编写的教学证据；真实网页搜索单独测试，没有将网页结果接成同一问题的完整自动补搜闭环。也没有把提示词发送给ChatGPT、Claude或Gemini。

## 用量与费用估算

模型调用13次（含首次失败的规划及一次有明确提示修改的复测），搜索1次；输入11,963 token，输出890 token。按[官方价格表](https://api-docs.deepseek.com/quick_start/pricing/)的峰值价格、全部输入按缓存未命中估算：

`11,963 × $0.30 / 1M + 890 × $1.20 / 1M + 1 × $0.00075 = $0.0054069`。

估算低于本轮$0.05上限。供应商返回部分缓存命中，因此这个数字采用保守口径；实际扣费以控制台账单为准。预算限制来自本次私有测试脚本，不表示产品已有通用美元硬限额功能。

## 仍需验证

规划只复测一次，不能说明错误率已经降低到某水平；同样保留无效输出单独分类。四类判定的代表性覆盖、中文与长证据质量、真实网页定向补搜的效果及浏览器插件三站点适配均未在本轮验收。自动化回归使用mock与回环服务，不重复产生付费调用。

[接入配置](../api_edition/config.deepseek.example.json) · [API说明](../api_edition/README.md) · [官方JSON模式](https://api-docs.deepseek.com/guides/json_mode/) · [官方搜索接口](https://api-docs.deepseek.com/guides/search/)
