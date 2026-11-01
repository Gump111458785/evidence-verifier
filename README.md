# Evidence Verifier

[![Release build](https://github.com/Gump111458785/evidence-verifier/actions/workflows/release-checks.yml/badge.svg)](https://github.com/Gump111458785/evidence-verifier/actions/workflows/release-checks.yml)

**面向多跳搜索智能体的证据审查工作台与 LLM 侧栏。**

将候选答案不可见的事实抽取、独立支持性核验、原文引用回放和跨关系变量绑定拆成可追溯的步骤。核验器指出缺失关系，再交给搜索工具定向补证。

[快速开始](#快速开始) · [架构](docs/architecture.md) · [API / SDK](docs/integration.md) · [交付说明](docs/delivery.md) · [Releases](https://github.com/Gump111458785/evidence-verifier/releases)

![证据审查工作台与外接侧栏](docs/images/plugin-completed.jpg)

## 交付能力

- **证据核验内核**：12 类关系合同，显式约束主客体角色、方向与时间；精确回放引用位置，使用白名单运算处理年份加减与区间。结果区分支持、缺失、矛盾与歧义，运行失败和无效输出单列。
- **联合实体绑定**：逐条成立不代表链条成立。保留中间实体候选，通过整条关系计划检查一致性，定位任职/教育混淆、角色颠倒和实体错接。
- **取证工作流与工程控制**：search / open / find、共享工具预算、缺口补搜、任务恢复和取消；版本化核验缓存、并发请求合并、按估算工作量分配模型副本。
- **LLM 附属接入**：Chrome Manifest V3 侧栏源码、独立 HTTP API、零依赖 Python 客户端与可选 LangGraph 节点；前端预览和插件共用界面。

**v0.1.0 是工程演示交付。** 默认无需 GPU、模型或第三方 Python 包，可回放两个教学场景和历史开发诊断。教学场景中的事实抽取与语义判断为预设，引用位置和联合绑定由程序计算；真实模型接入、训练及效果评测的状态见[交付说明](docs/delivery.md)。

## 快速开始

需要 Python 3.10+，以及 Python 自带且支持 FTS5 的 SQLite。

```bash
git clone https://github.com/Gump111458785/evidence-verifier.git
cd evidence-verifier
python3 app/server.py --port 8790
```

打开 **http://127.0.0.1:8790/#plugin**：

1. 选择“任职误推为就读”或“中间实体接错人”。
2. 点击“在侧栏审查示例”，查看每条关系、实际实体和原文。
3. 点击“补入关键教育证据”，查看同一位设计师的关系链如何连通。

运行数据在本机新建，默认位于 `app/data/workbench.sqlite`，不会上传。服务只监听本机；它面向个人工作区，未提供多用户身份认证，不应直接暴露到公网。

## 浏览器侧栏

从 [Release](https://github.com/Gump111458785/evidence-verifier/releases) 下载 `evidence-verifier-extension-0.1.0.zip`，解压后按[插件说明](app/extension/README.md)手动加载。也可以直接使用工作台内同界面的侧栏预览。

插件通过图标或用户主动选择文本的右键菜单打开。选中文本只存放在扩展会话内；当前版本回放教学案例，不自动扫描页面或为任意文本生成幻觉分数。本机服务不可用时显示离线快照。

## 接入现有智能体

其他项目可以提交证据和声明关系，无需使用工作台搜索页面：

```python
from evidence_verifier import VerifierClient

client = VerifierClient("http://127.0.0.1:8790")
result = client.verify(
    evidence=[{"id": "E1", "text": "Nora Hale attended Lake University as a student."}],
    relations=[{"subject": "Nora Hale", "predicate": "educated_at", "object": "Lake University"}],
    request_key="example-run-001",
)
```

该调用需要服务端已配置核验模型；默认 CPU 演示会明确返回模型未配置。客户端可以通过 Release wheel 在调用方自己的环境安装，也可使用 `PYTHONPATH=sdk/src`。详见[接入文档](docs/integration.md)。

## 数据与结果

仓库保留 **92 条构造开发诊断 × 3 个模型 × 2 种流程，共 552 份历史输出**，用于追溯与错误分析；不是独立测试集，也不是本次新训练的效果。原 14B 拆分流程为 **69/80 联合正确**，未通过既定门槛。完整口径和六组原始计数见[开发诊断记录](docs/evaluation.md)。

公开包不携带 MuSiQue 段落数据库、模型权重、训练 checkpoint 或私有运行数据库。无外部语料时，检索自动使用内置诊断段落；可选语料的来源和配置见[数据说明](docs/data.md)。

## 仓库结构

```text
app/                  工作台、核验/检索/任务服务与共享侧栏
  vendor/evpilot/      项目早期关系内核版本与诊断工具
  data/               构造开发输入、历史输出与门槛记录
  extension/          Chrome 侧栏入口与权限声明
sdk/                  Python 客户端、LangGraph 适配与示例
docs/                 架构、交付、接入、数据与实际截图
scripts/              离线交付构建
.github/workflows/    语法与交付构建检查
```

## 构建交付

```bash
python3 scripts/build_release.py --version 0.1.0 --out dist
# 若本机已有 setuptools / wheel，可另加 --wheel 构建客户端 wheel。
```

生成源码包、插件包、SDK 源码包与 SHA-256 清单，不下载模型、不安装依赖、不执行训练。CI 只检查语法和交付构建，不代表模型质量、GPU 兼容性或性能测试。

## License

自有代码采用 [MIT](LICENSE)。IBM Plex 字体沿用 [SIL Open Font License](app/static/fonts/LICENSE.txt)，数据与历史材料见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
