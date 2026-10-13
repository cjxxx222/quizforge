# 8. GPU 使用边界（RTX 4060）

- 状态：已接受
- 背景：本机为 RTX 4060 Laptop GPU（8GB），但 v1 主链路（DeepSeek API + CPU 解析/检索）不依赖 GPU。
- 决策：v1 保持"零 GPU 依赖"（这是约束下的理性选型，也是作品集加分项）；独显仅用于 M3 可选 RAG 讲解增强：BGE-M3 + bge-reranker-large 本地 CUDA 推理。
- 后果：v1 交付不依赖本地推理环境；M3 引入 Chroma + FlagEmbedding 后，评测对比"API rerank vs 本地 CUDA rerank"本身成为可写进报告的实验。
