# 能力状态与实现依据

本页记录各组件的实现与验收范围。模型实测、工程合同和研究计划分别列出。更新日期：2026-11-02。

状态统一为：**已实现**＝有实现及对应程序/合同测试；**进行中**＝已有代码或数据，但所述验收未完成；**未实现**＝规划，不是交付能力。模型效果与工程合同分开列出。

| 能力 | 状态与证据 |
|---|---|
| 关系角色、方向、时间约束与跨关系绑定 | **已实现**：[内核](../app/relations.py)、[角色/错绑回归](../app/tests/test_relations.py)；12类关系定义见[CONTRACT](../app/vendor/evpilot/relation_kernel_v7.py) |
| 候选答案不可见的抽取输入 | **已实现（数据合同）**：[抽取路径](../app/engine.py)、[API消息隔离测试](../api_edition/tests/test_provider.py)；真实模型质量待测 |
| 原文位置回放与受限年份计算 | **已实现**：[位置校验](../app/engine.py)、[共享内核](../app/vendor/evpilot/relation_kernel_v7.py)、[历史记录回放检查](../app/tests/test_workbench.py) |
| 共享预算检索、缺口查询、补搜与重核验 | **已实现（程序/合同）**：[检索测试](../app/tests/test_retrieval.py)、[补证报告测试](../app/tests/test_sidecar.py)；真实端到端收益待测 |
| 版本缓存、并发合并、多副本路由 | **进行中**：[实现](../app/inference.py)，真实模型负载、吞吐与费用未测 |
| HTTP 核验、Python SDK、LangGraph 节点 | **进行中（接入验收）**：[API](../app/verification_api.py)、[SDK](../sdk)；代码交付不等于框架与真实服务已验收 |
| 纯 API 模式，独立规划/抽取/核验配置 | **已实现（合同及少量真实API冒烟）**：[启动器](../api_edition/run.py)、[基础记录](deepseek-smoke-2026-11-02.md)；[自动网页单开发案例](web-grounding-validation-2026-11-02.md)通过，代表性准确率待测 |
| 有界原文补齐与引用内独立核验 | **已实现（合同及单案例）**：[原文处理](../app/grounding.py)、[回归](../app/tests/test_grounding.py)；同文档邻句补齐，仍逐条核验语义，未修复错误保留为无效输出 |
| 浏览器侧栏与三站点适配 | **进行中（分站点验收）**：ChatGPT单案例交互链路通过；Gemini地区阻断；Claude未测。[实机记录](browser-validation-2026-11-02.md) |
| LoRA 预检与分块交叉熵 | **进行中（目标模型验收）**：[预检](../app/training.py)、[损失实现](../app/torch_training.py)、[测试与跳过条件](../app/tests/test_training.py)；不使用 Liger |
| 3B 专用核验模型 | **未实现**：目标训练尚未启动，没有交付 checkpoint 或训练收益 |
| 别名表/同名消歧、可靠自动轨迹过滤 | **未实现**：已有受限名称归一化和人工整理，不能替代这些能力 |
| 托管云 API、计费控制台、企业部署 | **未实现**：当前是个人本机工作区 |

## 设计原则

- **事实抽取不看候选或目标关系**；规划器与抽取器分别接收不同输入。[代码](../app/engine.py) / [合同测试](../api_edition/tests/test_provider.py)
- **位置命中只证明出处**；语义支持性单独核验，所有必要关系再做联合绑定。[代码](../app/relations.py) / [测试](../app/tests/test_relations.py)
- **失败不计作拒答成功**；无效输出、运行失败、缺失、矛盾与歧义分别记录。[代码](../app/sidecar.py) / [测试](../app/tests/test_sidecar.py)
- **输入和原文有明确上限**；不拿静默截断后的结果充作完整证据。[代码](../app/verification_api.py)
- **缓存绑定身份**；模型无固定 revision 时限制复用，API 搜索隔离账户与索引。[模型缓存](../app/engine.py) / [API检索测试](../api_edition/tests/test_provider.py)
- **研究版与 API 版不互相修改检索类**；API 未配置时不退回教学语料。[显式注入](../api_edition/runtime.py) / [测试](../api_edition/tests/test_provider.py)

## 验收与后续计划

[真实网页单案例](web-grounding-validation-2026-11-02.md) · [浏览器分站点记录](browser-validation-2026-11-02.md) · [历史诊断与计分](evaluation.md) · [后续门槛](roadmap.md)。

600题人工双标、300题同预算搜索对照尚未完成；目标模型训练尚未启动。缓存、并发合并及路由有实现和合同测试，显存、吞吐、P95延迟与费用收益仍需同负载实测。
