# AI 基础设施层 (infra.ai)

## 1. 模块定位

`ragent.infra.ai` 是 AI 基础设施层，对上层业务提供统一的 LLM 对话与文本向量化接口，对下层通过 **litellm** 屏蔽不同模型供应商的 API 差异。模块内置模型级熔断器与多候选路由降级机制，确保单供应商故障不影响整体服务。

**模块文件结构：**

| 文件 | 职责 |
|------|------|
| `models.py` | 模型候选、熔断器配置等 Pydantic 数据模型 + `ModelConfigManager` 配置管理器 |
| `model_selector.py` | `ModelSelector` 候选排序 + `ModelCircuitBreaker` 三态熔断器 |
| `routing_executor.py` | `RoutingExecutor` 通用降级编排器（普通 + 流式） |
| `probe_stream.py` | `ProbeStreamBridge` 流式首包探测桥接器 |
| `llm_service.py` | `LLMService` LLM 对话服务 |
| `embedding_service.py` | `EmbeddingService` 向量嵌入服务 |

---

## 2. 三大 AI 能力

| 能力 | 业务接口 | litellm 映射 | 当前默认模型 |
|------|---------|-------------|-------------|
| Chat（对话） | `LLMService` | `litellm.acompletion` | `openai/glm-4-flash`（智谱） |
| Embedding（向量化） | `EmbeddingService` | `litellm.aembedding` | `openai/Qwen/Qwen3-Embedding-8B`（硅基流动） |
| Rerank（重排序） | 预留接口（`rerank_models`） | — | 暂无默认配置 |

> **说明：** 当前代码中 `TaskType` 枚举已定义 `RERANK = "rerank"`，`ModelConfig` 预留了 `rerank_models` 字段，但尚未实现独立的 RerankService。

---

## 3. 模型配置管理

### 3.1 数据模型

核心 Pydantic 模型定义在 `models.py` 中：

```
ModelConfig                     # 模型路由总配置
├── chat_models: list[ModelCandidate]       # Chat 候选列表
├── embedding_models: list[ModelCandidate]  # Embedding 候选列表
├── rerank_models: list[ModelCandidate]     # Rerank 候选列表（预留）
├── circuit_breaker: CircuitBreakerConfig   # 熔断器参数
└── stream: StreamConfig                    # 流式响应参数
```

**`ModelCandidate` 字段：**

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `model_name` | `str` | — | litellm 兼容的模型名称（如 `"openai/glm-4-flash"`） |
| `provider` | `str` | `""` | 供应商名称（可选，用于日志标识） |
| `priority` | `int` | `0` | 优先级，数值越小优先级越高 |
| `timeout` | `float` | `30.0` | 单次请求超时（秒） |
| `max_retries` | `int` | `2` | 最大重试次数 |
| `enabled` | `bool` | `True` | 是否启用 |

### 3.2 ModelConfigManager

`ModelConfigManager` 提供两种初始化方式：

**方式一：环境变量默认值（默认）**

```python
mgr = ModelConfigManager()
# 从 settings 读取 GLM_MODEL / EMBEDDING_MODEL 构建单候选配置
# Chat → settings.GLM_MODEL（openai/glm-4-flash，provider=zhipu）
# Embedding → settings.EMBEDDING_MODEL（openai/Qwen/Qwen3-Embedding-8B，provider=zhipu）
# Rerank → 空列表
```

**方式二：YAML 配置文件**

```python
mgr = ModelConfigManager.from_yaml("config/models.yaml")
```

YAML 示例：

```yaml
chat_models:
  - model_name: openai/glm-4-flash
    provider: zhipu
    priority: 0
    timeout: 30.0
  - model_name: openai/glm-4-air
    provider: zhipu
    priority: 1
    timeout: 60.0

embedding_models:
  - model_name: openai/Qwen/Qwen3-Embedding-8B
    provider: siliconflow
    priority: 0

circuit_breaker:
  failure_threshold: 5
  recovery_timeout: 60
  success_threshold: 3

stream:
  first_packet_timeout: 60
```

**获取候选列表：**

```python
candidates = mgr.get_candidates("chat")       # 返回 chat_models 副本
candidates = mgr.get_candidates("embedding")  # 返回 embedding_models 副本
candidates = mgr.get_candidates("rerank")     # 返回 rerank_models 副本
```

---

## 4. 模型选择器与熔断器

### 4.1 ModelSelector

`ModelSelector` 位于 `model_selector.py`，负责按任务类型筛选并排序可用模型候选：

```
select_candidates(task_type)
  │
  ├── 1. 从 ModelConfigManager 获取原始候选列表
  ├── 2. 过滤掉 enabled=False 的候选
  ├── 3. 过滤掉熔断器处于 OPEN 状态的候选
  ├── 4. 按 priority 升序排序
  └── 5. 返回可用候选列表（无可用时抛出 RemoteException C3001）
```

此外提供 `record_success(model_name)` 和 `record_failure(model_name)` 方法供调用层更新熔断器状态。

### 4.2 三态熔断器 (ModelCircuitBreaker)

每个模型维护独立的熔断器实例，状态转换规则：

```
         连续失败 ≥ failure_threshold           冷却期结束
    ┌──────────┐  ──────────────────▶  ┌──────────┐  ──────────▶  ┌───────────┐
    │  CLOSED  │                       │   OPEN   │              │ HALF_OPEN │
    │ (正常)    │  ◀──────────────────  │ (熔断)    │  ◀──────────  │ (探测)     │
    └──────────┘   连续成功 ≥ success   └──────────┘   探测失败     └───────────┘
                   _threshold (从 HALF_OPEN→CLOSED)
```

**熔断器参数（通过 `CircuitBreakerConfig` 配置）：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `failure_threshold` | `5` | 连续失败多少次后触发熔断（CLOSED → OPEN） |
| `recovery_timeout` | `60` | OPEN 状态冷却时间（秒），之后自动转为 HALF_OPEN |
| `success_threshold` | `3` | HALF_OPEN 状态下连续成功多少次后恢复 CLOSED |

> 注意：HALF_OPEN 状态下任意一次失败都会立即回到 OPEN。

---

## 5. LLM 服务

### 5.1 LLMService 概述

`LLMService` 位于 `llm_service.py`，封装 `litellm.acompletion` 调用，提供两个核心接口：

| 方法 | 说明 |
|------|------|
| `chat(messages, *, model, temperature, max_tokens)` | 非流式对话，返回完整响应文本 |
| `stream_chat(messages, *, model, temperature, max_tokens)` | 流式对话，异步生成器逐 token 返回 |

### 5.2 调用模式

**指定模型（跳过路由）：**

当传入 `model` 参数时，直接使用该模型调用，不经过路由降级和熔断器。

```python
reply = await service.chat(messages, model="glm-4-flash")
```

**未指定模型（路由降级）：**

不传 `model` 时，通过 `ModelSelector.select_candidates("chat")` 获取候选列表，按优先级依次尝试：

1. 候选 1 调用 → 成功 → `record_success` → 返回结果
2. 候选 1 调用 → 失败 → `record_failure` → 尝试候选 2
3. 全部失败 → 抛出 `RemoteException(error_code="C3001")`

### 5.3 API 调用细节

- **统一使用 `litellm.acompletion`** 进行调用
- API Key / Base URL 取自 `settings.GLM_API_KEY` / `settings.GLM_BASE_URL`（智谱）
- 流式调用时设置 `stream=True`，通过 `chunk.choices[0].delta.content` 逐 token 提取
- 路由降级时使用候选的 `timeout` 值作为请求超时

### 5.4 流式降级的特殊处理

非流式路由降级会遍历所有候选直到成功；流式路由降级（`_stream_with_fallback`）同样会遍历候选，但对每个候选使用 `has_produced` 标志跟踪是否成功产出了 token：
- 如果成功产出过 token，记录成功并结束
- 如果候选失败，记录失败并尝试下一个候选

---

## 6. Embedding 服务

### 6.1 EmbeddingService 概述

`EmbeddingService` 位于 `embedding_service.py`，封装 `litellm.aembedding` 调用：

| 方法 | 说明 |
|------|------|
| `embed(text, *, model)` | 单文本向量化，返回 `list[float]` |
| `embed_batch(texts, *, model)` | 批量文本向量化，返回 `list[list[float]]` |

> `embed` 内部调用 `embed_batch([text])` 并取第一个结果。

### 6.2 API 路由策略

Embedding 服务使用 **硅基流动 (SiliconFlow)** 作为主要 API 提供商：

```python
emb_api_key = settings.SILICONFLOW_API_KEY or settings.GLM_API_KEY
emb_api_base = settings.SILICONFLOW_API_BASE or settings.GLM_BASE_URL
```

即：优先使用 SiliconFlow 的 API Key/Base，若未配置则回退到智谱。

### 6.3 降级逻辑

与 LLM 服务类似：
- **指定模型**：直接调用 `_call_embedding_direct`，跳过路由
- **未指定模型**：通过 `_call_embedding_with_fallback` 走路由降级

降级流程：
1. `select_candidates("embedding")` 获取候选列表
2. 按优先级依次调用 `litellm.aembedding`
3. 成功 → `record_success` → 按 index 排序后返回向量列表
4. 失败 → `record_failure` → 尝试下一个
5. 全部失败 → `RemoteException(error_code="C3001")`

### 6.4 默认模型

当前默认 Embedding 模型为 `openai/Qwen/Qwen3-Embedding-8B`，通过硅基流动 API 调用。

---

## 7. 配置项

所有配置通过环境变量注入（`.env` 文件），定义在 `config/settings.py` 中：

### AI 模型配置

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `GLM_API_KEY` | `""` | 智谱 AI API Key |
| `GLM_BASE_URL` | `https://open.bigmodel.cn/api/coding/paas/v4` | 智谱 API Base URL |
| `GLM_MODEL` | `openai/glm-4-flash` | 默认 Chat 模型（litellm 格式，带 provider 前缀） |
| `EMBEDDING_MODEL` | `openai/Qwen/Qwen3-Embedding-8B` | 默认 Embedding 模型（litellm 格式） |
| `SILICONFLOW_API_KEY` | `""` | 硅基流动 API Key（用于 Embedding） |
| `SILICONFLOW_API_BASE` | `https://api.siliconflow.cn/v1` | 硅基流动 API Base URL |

### 熔断器配置（YAML 或代码配置）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `circuit_breaker.failure_threshold` | `5` | 连续失败触发熔断的阈值 |
| `circuit_breaker.recovery_timeout` | `60` | 熔断冷却时间（秒） |
| `circuit_breaker.success_threshold` | `3` | 半开状态下恢复所需的连续成功次数 |

### 流式配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `stream.first_packet_timeout` | `60.0` | 流式首包超时时间（秒） |

---

## 8. 扩展点

### 8.1 新增 LLM 供应商

1. **确认 litellm 支持**：litellm 内置 100+ 供应商，新供应商只需使用正确的 `model_name` 前缀即可（如 `openai/gpt-4`、`anthropic/claude-3`）
2. **添加候选配置**：在 YAML 或代码中向 `chat_models` 添加新的 `ModelCandidate`，设置合适的 `priority` 和 `timeout`
3. **配置 API Key**：如需新的认证信息，在 `settings.py` 中添加环境变量，并在 `LLMService` 调用时传入

示例 YAML：

```yaml
chat_models:
  - model_name: openai/glm-4-flash
    provider: zhipu
    priority: 0
  - model_name: openai/gpt-4o
    provider: openai
    priority: 1       # 备选，优先级低
    timeout: 60.0
```

### 8.2 新增 Embedding 供应商

1. 在 `embedding_models` 中添加新的 `ModelCandidate`
2. 如使用不同 API Key/Base，在 `settings.py` 中新增配置项，并在 `EmbeddingService._call_embedding_direct` / `_call_embedding_with_fallback` 中调整 Key 取值逻辑

### 8.3 实现 Rerank 服务

1. 在 YAML 的 `rerank_models` 中配置候选模型
2. 参照 `LLMService` / `EmbeddingService` 的模式，新建 `RerankService` 类
3. 使用 `litellm.rerank` 进行调用，结合 `ModelSelector` 实现降级

### 8.4 自定义路由策略

可继承或替换 `ModelSelector`，实现自定义的候选排序逻辑（如基于延迟、成本、负载均衡等策略），然后注入到 `LLMService` / `EmbeddingService` 中。

### 8.5 自定义熔断策略

修改 `CircuitBreakerConfig` 参数或继承 `ModelCircuitBreaker`，实现更细粒度的熔断控制（如按模型独立配置不同阈值）。
