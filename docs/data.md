# Data and provenance

## 随源码交付

- app/data/archive.json：92条构造开发输入，以及Qwen 7B、Llama 8B、Qwen 14B在direct/split两种条件下共552份历史输出。
- app/data/development-gate-v7-qwen14b.json：原14B门槛记录，保留失败状态。
- plugin_scenarios.py、scenarios.py及configs中的fixture：项目自编教学材料，不进入正式训练集。

旧源归档SHA-256记录为`f85a8708b75368a7e42ab4dffb94a98e3e2458f12279b3df7de6dbaf9d3e11a9`；它是原始归档身份，不是当前源码ZIP的摘要。当前分发文件摘要见Release的SHA256SUMS。

公开仓库不包含个人标注文件、运行SQLite、模型权重、适配器或服务器日志。历史构造样本用于开发分析，不能重新包装为未见测试。

## 可选语料

本地原部署曾使用MuSiQue-Ans v1.0开发集的21,100段只读索引。本次公开包不分发该索引，也不宣称使用其训练集完成训练。

若自行准备兼容FTS5索引，可将EV_CORPUS指向本机文件：需要search_index表，包含doc_id、title、text，并支持bm25(search_index,0,2,1)。数据获取、许可与署名由使用者按上游要求处理。无此文件时，服务使用内置诊断段落作为小型检索库。

未来独立验证、真实正例留出及训练数据需另行构建；仓库中的开发材料和教学案例不能替代它们。
