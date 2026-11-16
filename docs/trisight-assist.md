# Trisight 审阅接口（v0.5.0）

三种模式共用一个接口。所有接口需要受邀账号会话或服务端访问凭证，`/api/library` 除外。

## 创建与推进

`POST /api/assist`

| 字段 | 说明 |
|---|---|
| `question` | 必填，最多 1,000 字 |
| `answer` | 必填，最多 5,000 字 |
| `context` | 可选，`[{role: "user"\|"assistant", text}]`，最多 10 轮、合计 2,000 字 |
| `sources` | 可选，`[{text, url?, title?}]`，最多 3 段、合计 4,000 字；提供后纠错只用它核对，不再检索 |
| `modes` | `"auto"`（默认），或 `["correct","supplement","predict"]` 的非空子集 |
| `compose` | 是否生成修订后的回答，默认 `true`；插件传 `false`，改用提示词 |
| `client` | `web` / `extension` / `sdk` |
| `request_key` | 可选，相同输入重复提交时返回同一任务 |

超限输入直接拒绝，不截断。返回的任务带 `advance_path`；每次 `POST advance_path` 至多发起一次付费调用（模型或检索），`GET /api/assist/{id}` 不产生调用。

| 阶段 | 调用 | 说明 |
|---|---|---|
| `review` | 模型 1 次 | 判定三种模式是否需要；给出最多 4 条断言、最多 4 个遗漏点、最多 3 个下一问 |
| `search` | 检索 0–2 次 | 为断言和事实类遗漏点检索原文；超出上限的项标为“未检索” |
| `verify` | 模型 0–1 次 | 只依据证据判定 supported / contradicted / insufficient |
| `compose` | 模型 0–1 次 | 仅在发现错误或遗漏、且 `compose=true` 时运行 |

## 结果中的约束

- 断言必须是原回答的连续原文片段，记录 `span`；不是原文片段的断言不核验，标为 `invalid_output`。
- supported 与 contradicted 必须附引文，引文须在模型看到的证据中逐字找到；找不到时标为 `invalid_output`，不改记为证据不足。
- 纠错总结论：`errors_found`、`no_error`（全部断言有证据支持）、`unverified`（有未能核实的断言）、`no_checkable_claims`、`not_run`。
- 补充点的 `source_status`：`not_required`（解释类）、`sourced`、`unsourced`（事实类但未找到来源）。
- 预测只返回问题，不生成答案。
- 修订稿中的每处改动必须引用已有的审阅结果编号；引用不存在的编号时修订稿记为无效。
- 当前只检查开放式断言（`tier: open_claim`），12 类结构化关系内核尚未接入。

## 其他接口

| 接口 | 说明 |
|---|---|
| `GET /api/assist?offset=&limit=` | 本人的审阅历史摘要 |
| `POST /api/assist/{id}/feedback` | `{target: correct\|supplement\|predict\|answer, value: up\|down, note?}`，同一对象只保留最新一次，并记录当时的研究授权状态 |
| `GET` / `POST /api/consent` | 研究数据授权，`{research: true\|false}`，可随时撤回 |
| `GET /api/library` | 公开案例库；全部为项目团队编写的演示案例，结果为预设，不收录用户记录 |

## 费用

模型与检索共用全站试用账本，每次调用预留 ¥0.25。一次完整审阅最多 3 次模型调用和 2 次检索，即最多预留 ¥1.25。
