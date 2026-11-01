# Evidence Verifier Python client 0.2.0

零第三方依赖的Python 3.10+客户端。提交证据与声明关系，恢复同一任务，不把观察超时、失败或无效输出计作证据不足。

```python
from evidence_verifier import VerifierClient, ModelUnavailable
client = VerifierClient("http://127.0.0.1:8790")
try:
    result = client.verify(
        [{"id": "E1", "text": "Nora Hale attended Lake University as a student."}],
        [{"subject": "Nora Hale", "predicate": "educated_at", "object": "Lake University"}],
        request_key="caller-check-001",
    )
except ModelUnavailable:
    print("请先连接核验模型；默认CPU演示不会伪造模型结果。")
```

该示例需要已配置模型。源码使用PYTHONPATH=sdk/src，或把Release中的wheel安装到调用方自己的环境；本包未发布PyPI。

| 方法 | 行为 |
|---|---|
| health() | 只读配置，不调用模型 |
| submit(evidence, relations, request_key=...) | 创建/找回任务 |
| get(job_id) | 恢复原始状态与结果 |
| wait(job_id, timeout=...) | 等待同一任务，超时保留身份 |
| verify(...) | submit与wait的组合 |
| cancel(job_id) | 显式取消指定任务 |

ModelUnavailable、JobFailed、JobCanceled、InvalidOutput、WaitTimeout分别保留含义。提交网络错误的VerifierTransportError保留request_key，不能因此自动创建另一个任务。更换输入或模型版本需新的逻辑键。

## 可选LangGraph适配

```python
from evidence_verifier.langgraph import build_verification_graph, new_verification_input
from langgraph.checkpoint.memory import InMemorySaver

graph = build_verification_graph(client, checkpointer=InMemorySaver(), timeout=30)
state = new_verification_input(evidence, relations, key="business-check-001")
result = graph.invoke(state, {"configurable": {"thread_id": "business-run-001"}})
```

构图时才导入调用方已有的LangGraph，不自动安装。节点保存任务ID、输入/服务摘要与逻辑键；pending表示等待，不能当作missing。InMemorySaver不保证跨进程恢复，需要调用方提供持久checkpointer。当前适配器交付源码，未完成真实框架环境运行验证。

完整示例在examples/。声明关系成立不证明问题覆盖完整，也不证明证据本身客观真实；见[接入指南](../docs/integration.md)。
