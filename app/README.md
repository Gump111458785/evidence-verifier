# Evidence Verifier workbench

```bash
python3 app/server.py --port 8790
```

打开http://127.0.0.1:8790/#plugin。默认只需Python标准库，回放教学案例和历史开发记录，不加载模型。详细功能、来源与能力范围见[项目README](../README.md)、[交付说明](../docs/delivery.md)和[接入文档](../docs/integration.md)。

数据写入本机app/data/workbench.sqlite，可通过EV_DB指定独立路径。未携带可选MuSiQue数据库时，检索使用内置诊断段落。不要将个人工作台直接暴露到公网。
