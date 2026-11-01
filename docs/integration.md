# Integration

## 默认演示

`GET /api/plugin-demo`返回两组教学案例和程序绑定结果。响应携带`source=ideal_plugin_demo`、`measured=false`、`model_inference_performed=false`。无需模型即可运行。

网页侧栏与MV3扩展使用相同的plugin.html/js/css。扩展仅接收用户主动选中的文本，保存在session storage；当前不将其提交模型。构建及权限见[扩展README](../app/extension/README.md)。

## 真实核验接口

先连接已有OpenAI兼容服务；程序不会自动安装或下载模型。可从根目录的.env.example复制配置，但服务不自动读取.env文件，需由调用方明确导出所需环境变量。独立核验需要调用方环境中已有jsonschema；训练另需torch/transformers/peft。

```bash
export EV_MODEL_URL=http://127.0.0.1:8001/v1
export EV_MODEL_NAME=your-existing-verifier-model
export EV_MODEL_REVISION=your-pinned-revision
python3 app/server.py --port 8790
```

```json
{
  "evidence": [{"id":"E1","text":"Nora Hale attended Lake University as a student."}],
  "relations": [{"subject":"Nora Hale","predicate":"educated_at","object":"Lake University"}],
  "request_key": "caller-owned-check-001"
}
```

| API | 行为 |
|---|---|
| GET /api/health | 配置状态与语料来源，不调用模型 |
| POST /api/verifications | 提交上述输入，返回持久化任务ID |
| GET /api/verifications/:id | 恢复状态、原始输入与结果 |
| POST /api/verifications/:id/cancel | 只取消指定任务，当前HTTP调用可能先返回 |
| POST /api/workflows | 创建共享工具预算的搜索/核验/补搜任务 |
| GET /api/workflows/:id | 读取阶段记录、来源与已知成本 |
| GET /api/inference-status | 进程内排队、执行与估算工作量 |

相同request_key与相同输入找回原任务；输入改变则拒绝键复用。观察超时不创建新任务。失败、取消、无效输出与pending分别记录，不能归入missing。

证据限制1–40段、合计22,000字符，关系1–8条。暂不支持的关系、限定条件不应悄悄忽略。具体支持谓词与角色见内核CONTRACT。

## Python / LangGraph

客户端0.2.0基础包只依赖标准库，见[sdk/README.md](../sdk/README.md)。LangGraph适配在调用构图函数时才导入调用方已有框架，当前版本未完成真实框架环境验证。

发布wheel可安装到调用方独立环境；这不是PyPI发布。核验模型未配置时客户端抛出ModelUnavailable，不输出预填的成功结果。
