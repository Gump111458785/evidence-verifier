# Evidence Verifier — Pure API Edition

本目录提供独立的纯API启动方式，复用同仓库的关系合同、引用回放、跨关系绑定和侧栏。推理与网页搜索均由配置的远程API完成；不加载本地权重、不使用GPU、不执行SFT/LoRA/RL训练。原开源模型研究版与历史结果保留，不代表其训练已经完成。

## 启动

Python 3.10+。在自己的独立虚拟环境安装CPU端JSON Schema校验依赖：

```bash
python3 -m venv .venv-api
.venv-api/bin/python -m pip install -r api_edition/requirements.txt
cp api_edition/config.example.json api_edition/config.local.json
```

编辑config.local.json：为需要的角色填写`base_url`（供应商的基础地址，程序追加/chat/completions）、`model`和`api_key_env`。仅支持OpenAI兼容Chat Completions协议；Anthropic/Gemini原生端点没有在此版本中实现，需其兼容端点或已有网关。通用示例没有预填厂商模型名或任何真实密钥。

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
- 每角色可显式配置reasoning_effort为none、low、high或max；只在该供应商支持时使用，不配置则不主动添加此参数。
- 每角色可显式配置max_output_tokens（128–16384的整数），覆盖调用方输出上限，并按token_parameter映射。思考模型的思考token也会消耗供应商输出额度；增加额度不保证返回有效JSON，禁止依赖无上限重试。[本次真实截断记录](../docs/browser-validation-2026-11-02.md)。
- HTTPS连接；HTTP只允许回环地址，供自有网关或合同演练。拒绝携带凭据的URL、密钥内联配置及重定向，不转发凭据到意外目的地。
- 超时、限流、拒绝、空回答、无效JSON/引用都不是“证据不足”。无自动付费重试。
- 顶层`grounding_repair_attempts`只允许0或1，默认0。设为1时，对实体未在引用中定位的事实先尝试同文档前后各2句、额外至多600字符的原文补齐，保留所有中间句；仍无效时至多调用一次extractor修正定位。它只看证据、关系定义、原假设与错误，不看问题、候选或目标关系；不许丢弃事实或更改关系、极性、时间限定。该额外模型阶段可能计费，网络错误仍不重试。补齐出处不等于支持，之后只用各事实所引句独立核验语义。
- 本地仅启动私有回环API桥接和工作台；桥接有随机进程内令牌，真实密钥不传给浏览器、客户端或任务记录。
- API没有可信不可变模型版本时不跨运行复用核验结果；供应商token统计缺失时成本未知，不填零费用。

## 搜索API预留协议

search.endpoint是完整HTTPS地址，密钥通过所指环境变量作为Bearer发送。

请求：`{"query":"...","max_results":3,"include_raw_content":true}`。

响应：`{"results":[{"url":"https://example.org/article","title":"来源","raw_content":"来源正文","score":1.0}]}`。

这是默认generic协议；必须由供应商或调用方网关兼容。generic协议下只有content/snippet的结果不会进入证据库。设置`search.protocol=deepseek`时使用原生`query/count/need_content`请求，将content正文及rerank_score映射为来源；snippet/summary仍不充当正文。包含实体锚点的DeepSeek查询超过128字符时明确失败，不静默截断。

程序不根据任意URL抓取网页；open/find针对该API已经返回并存留的正文执行，保留URL、字符范围和工具预算。原工作台的open/find有片段长度范围，后续find可取其他位置，不将片段视为整页。search未配置时明确失败，绝不回退开发诊断语料。默认源内容不是客观真伪金标。

## DeepSeek 接入与实际冒烟测试

可复制[DeepSeek示例配置](config.deepseek.example.json)为config.local.json，设置环境变量`EV_API_VERIFIER_KEY`，再按上面的命令启动。四个角色可共用该密钥或自行分开。当前示例选择deepseek-flash、json_object、reasoning_effort=none，不发送seed；搜索使用官方原生端点。供应商型号和价格可能变化，使用前参考[官方价格表](https://api-docs.deepseek.com/quick_start/pricing/)。`.env.local`仅用于保存本地密钥，启动器不会自动加载；运行前须在当前进程环境设置对应变量，切勿上传密钥文件。

2026-11-02已完成真实付费API冒烟：三组自编核验样例、规划修复后的单次复测、网页搜索与补充提示词生成通过。最初规划输出有位置错误并被拒绝，记录保留；没有降低校验规则。[测试范围、费用与已知边界](../docs/deepseek-smoke-2026-11-02.md)。

后续以同一缺证问题验证真实网页自动补搜：`missing → search/open → 原文补齐 → 独立核验 → entailed → 补充prompt`单案例通过；DeepSeek示例现显式启用上述有界修正。成功轮只用了程序补齐，没有调用定位修正模型；此前失败保留。[完整记录及累计费用](../docs/web-grounding-validation-2026-11-02.md)。此为后端自动流程验收，不代表把prompt自动发送给原AI，也不代表三站点兼容或准确率提升。

## 检查与交付边界

自动化合同测试使用mock/回环服务，不调用付费厂商API；另有上述DeepSeek真实冒烟记录。尚未完成代表性数据集准确率、完整网页补搜闭环收益或三站点实机兼容评测，不能将少量样例通过率作为准确率。

API版下载包为`evidence-verifier-api-0.3.3-source.zip`；不包含训练入口文件，但保留共享核验内核与原始开发记录。API应用仍有Python/SQLite后端，不等于纯浏览器无服务器版本。

## 工程隔离与配置校验

API检索使用显式注入的独立会话实例，不修改研究版SearchSessions类。配置会复制并严格校验字段类型、有限数字和端口；拒绝非对象响应、拒绝/截断completion和无效搜索分数。未固定索引版本时限制跨运行缓存；已固定版本时仍将凭据身份摘要纳入检索缓存，不在记录中保存密钥。

环境隔离会清除旧模型、规划/智能体、路由和扩展授权设置，只使用当前API配置；保留调用方显式指定的运行数据库。合同测试不代表真实供应商兼容或效果评测。
