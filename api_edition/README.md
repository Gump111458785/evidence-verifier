# Evidence Verifier — Pure API Edition

本目录提供独立的纯API启动方式，复用同仓库的关系合同、引用回放、跨关系绑定和侧栏。推理与网页搜索均由配置的远程API完成；不加载本地权重、不使用GPU、不执行SFT/LoRA/RL训练。原开源模型研究版与历史结果保留，不代表其训练已经完成。

## 启动

Python 3.10+。在自己的独立虚拟环境安装CPU端JSON Schema校验依赖：

```bash
python3 -m venv .venv-api
.venv-api/bin/python -m pip install -r api_edition/requirements.txt
cp api_edition/config.example.json api_edition/config.local.json
```

编辑config.local.json：为需要的角色填写`base_url`（截至/v1，程序追加/chat/completions）、`model`和`api_key_env`。仅支持OpenAI兼容Chat Completions协议；Anthropic/Gemini原生端点没有在此版本中实现，需其兼容端点或已有网关。这里没有预填厂商模型名或任何真实密钥。

```bash
export EV_API_PLANNER_KEY='填写自己的密钥'
export EV_API_EXTRACTOR_KEY='填写自己的密钥'
export EV_API_VERIFIER_KEY='填写自己的密钥'
# 可选：EV_API_AGENT_KEY、EV_API_SEARCH_KEY
.venv-api/bin/python api_edition/run.py --config api_edition/config.local.json --check
.venv-api/bin/python api_edition/run.py --config api_edition/config.local.json --port 8790
```

打开http://127.0.0.1:8790/sidecar.html，或使用Release内的Chrome插件。插件需手动加载，并在API配置的extension_id填写实际32位扩展ID。默认示例全部留空，`--check`不发网络请求，只报告配置是否齐全；它不验证密钥有效性、额度、API兼容性或模型质量。未配置时不会假装运行成功。

## 独立角色与输入边界

| 角色 | 输入与功能 |
|---|---|
| planner | 问题、候选及关系定义；不接收证据，生成可审阅关系计划 |
| extractor | 仅证据与关系定义；不接收问题、候选或目标关系 |
| verifier | 抽取事实及其证据，独立判定支持性 |
| agent（可选） | 通过API编排search/open/find，生成搜索候选 |
| search（可选） | 经配置API查询并取得来源正文，供缺口补搜 |

planner/extractor/verifier可以使用不同API模型。只有extractor与verifier配置齐全才开放真实核验；规划和搜索智能体分别检查各自配置。密钥仅从环境读取，不能放入JSON配置。API版启动时隔离旧EV_MODEL/AGENT/PLANNER环境，不会误连旧本地模型服务。

## API兼容选项

- 每角色response_format可取json_schema、json_object、none。后两种模式将所需JSON结构放入system提示，返回后仍由本地内核验证，不因为取消远端约束而信任结果。
- 默认不发送seed；token_parameter可选max_tokens或max_completion_tokens。服务不支持某参数时应显式改配置，不自动切换协议、重试或重复计费。
- HTTPS连接；HTTP只允许回环地址，供自有网关或合同演练。拒绝携带凭据的URL、密钥内联配置及重定向，不转发凭据到意外目的地。
- 超时、限流、拒绝、空回答、无效JSON/引用都不是“证据不足”。无自动付费重试。
- 本地仅启动私有回环API桥接和工作台；桥接有随机进程内令牌，真实密钥不传给浏览器、客户端或任务记录。
- API没有可信不可变模型版本时不跨运行复用核验结果；供应商token统计缺失时成本未知，不填零费用。

## 搜索API预留协议

search.endpoint是完整HTTPS地址，密钥通过所指环境变量作为Bearer发送。

请求：`{"query":"...","max_results":3,"include_raw_content":true}`。

响应：`{"results":[{"url":"https://example.org/article","title":"来源","raw_content":"来源正文","score":1.0}]}`。

这是可配置的正文搜索接口合同；必须由供应商或调用方网关兼容。仅有content/snippet的结果不会进入证据库。程序不根据任意URL抓取网页；open/find针对该API已经返回并存留的正文执行，保留URL、字符范围和工具预算。原工作台的open/find有片段长度范围，后续find可取其他位置，不将片段视为整页。search未配置时明确失败，绝不回退开发诊断语料。默认源内容不是客观真伪金标。

## 检查与交付边界

已有纯API配置、输入边界、缺失服务、拒绝响应、snippet排除和本机桥接的合同测试。测试使用mock/回环服务，没有调用付费厂商API；没有进行真实供应商端到端兼容、准确率或费用评测。侧栏三站点实机兼容状态不因更换后端而改变。

API版下载包为`evidence-verifier-api-0.3.0-source.zip`；不包含训练入口文件，但保留共享核验内核与原始开发记录。API应用仍有Python/SQLite后端，不等于纯浏览器无服务器版本。
