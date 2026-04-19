# Ragent 系统架构文档

## 1. 项目概述

Ragent 是一个企业级 RAG（Retrieval-Augmented Generation）智能体平台，基于 Java 17 + Spring Boot 3 + React 18 构建。系统覆盖了从文档入库到智能问答的全链路能力，包括文档解析、分块策略、多路检索、意图识别、问题重写、会话记忆、模型容错、MCP 工具调用和全链路追踪等核心功能。

---

## 2. 技术栈总览

| 层面 | 技术选型 |
|---|---|
| 后端框架 | Java 17、Spring Boot 3.5.7、MyBatis Plus |
| 前端框架 | React 18、Vite 5、TypeScript、Tailwind CSS |
| 关系数据库 | PostgreSQL（20+ 张业务表） |
| 向量数据库 | Milvus 2.6 / PostgreSQL pgvector（双引擎支持） |
| 缓存与分布式锁 | Redis + Redisson |
| 对象存储 | S3 兼容存储（RustFS） |
| 消息队列 | RocketMQ 5.x |
| 文档解析 | Apache Tika 3.2 |
| 模型供应商 | 百炼（阿里云）、SiliconFlow、Ollama（本地） |
| 认证鉴权 | Sa-Token |
| 代码规范 | Spotless（自动格式化 + License Header） |

---

## 3. 系统整体架构

### 3.1 模块分层

Ragent 采用前后端分离的单体架构，后端按职责分为四个 Maven 子模块，外加一个独立的前端工程：

```
┌─────────────────────────────────────────────────────────────┐
│                    Ragent 系统全景                            │
├─────────────┬───────────────────────────────────────────────┤
│   前端层     │  React 18 + Vite + TypeScript + Tailwind      │
│  (frontend)  │  Zustand 状态管理 / Axios + SSE 通信          │
├─────────────┼───────────────────────────────────────────────┤
│   应用层     │  bootstrap（Spring Boot 启动模块）             │
│ (bootstrap)  │  用户管理 / 知识库 / RAG问答 / 入库流水线 /    │
│              │  意图树 / 链路追踪 / 管理后台                   │
├─────────────┼───────────────────────────────────────────────┤
│   AI基础设施  │  infra-ai（模型调用抽象层）                    │
│  (infra-ai)  │  Chat / Embedding / Rerank 三大能力            │
│              │  模型路由 + 熔断降级 + 首包探测                  │
├─────────────┼───────────────────────────────────────────────┤
│   通用框架   │  framework（横切关注点基础设施）                │
│ (framework)  │  异常体系 / 幂等 / 分布式ID / 用户上下文 /     │
│              │  链路追踪 / SSE封装 / MQ封装                    │
├─────────────┼───────────────────────────────────────────────┤
│   MCP服务    │  mcp-server（独立 Spring Boot 应用）           │
│ (mcp-server) │  JSON-RPC 2.0 / 工具注册与执行 / 天气/工单/销售 │
└─────────────┴───────────────────────────────────────────────┘
```

### 3.2 模块依赖关系

```
                    ┌──────────┐
                    │ frontend │
                    └────┬─────┘
                         │ HTTP / SSE
                    ┌────▼─────┐
                    │bootstrap  │
                    └──┬────┬──┘
                  ┌────┘    └────┐
           ┌──────▼──┐    ┌─────▼───┐
           │infra-ai │    │framework│
           └────┬────┘    └─────────┘
           ┌────▼────┐
           │framework│
           └─────────┘

           ┌───────────┐
           │mcp-server │  ← 独立进程，通过 HTTP 与 bootstrap 通信
           └───────────┘
```

依赖原则：
- **framework** 是最底层模块，不依赖任何业务模块，提供与业务无关的通用能力
- **infra-ai** 依赖 framework，屏蔽不同 AI 模型供应商的差异
- **bootstrap** 依赖 framework 和 infra-ai，包含全部业务逻辑
- **mcp-server** 是独立部署的 MCP 工具服务，通过 HTTP 协议与主应用交互
- **frontend** 通过 REST API 和 SSE 与后端通信

---

## 4. 各模块详细架构

### 4.1 Framework — 通用基础设施层

Framework 模块提供横切关注点的统一封装，确保业务模块只需关注业务逻辑本身。

```
┌──────────────────────────────────────────────────────────────────┐
│                      framework 模块                               │
├────────────┬────────────┬────────────┬───────────────────────────┤
│   异常体系  │  错误码规范  │  分布式ID   │      幂等框架              │
│ ClientEx   │ IErrorCode  │ Snowflake  │  IdempotentSubmit (HTTP) │
│ ServiceEx  │ BaseError   │ (Redis Lua │  IdempotentConsume (MQ)  │
│ RemoteEx   │ Code(A/B/C) │  初始化)    │  (AOP + Redis/Redisson)  │
├────────────┼────────────┼────────────┼───────────────────────────┤
│  用户上下文  │  链路追踪   │  SSE封装    │      MQ封装               │
│ UserContext │ @RagTrace  │ SseEmitter │  MessageWrapper          │
│ (TTL透传)   │ Root/Node  │ Sender     │  RocketMQProducerAdapter │
│ LoginUser   │ Context    │ (线程安全)  │  DelegatingTransaction   │
├────────────┼────────────┼────────────┼───────────────────────────┤
│  数据库配置  │  统一响应体  │  Redis命名  │     Spring上下文          │
│ MyMetaObj   │ Result<T>  │ KeySerial  │  ApplicationContextHolder│
│ Handler     │ Results    │ izer       │                          │
│ (自动填充)   │            │            │                          │
└────────────┴────────────┴────────────┴───────────────────────────┘
```

核心能力说明：

| 能力 | 实现方式 | 说明 |
|---|---|---|
| 三级异常体系 | ClientException / ServiceException / RemoteException | 对应客户端错误、服务端错误、远程调用错误 |
| 双维度幂等 | AOP + Redis（HTTP请求防重）/ AOP + Redis Lua（MQ消费防重） | 支持 SpEL 表达式动态生成幂等键 |
| Snowflake 分布式 ID | Redis Lua 原子分配 workerId + datacenterId | 集成 MyBatis Plus 自动填充主键 |
| 用户上下文透传 | TransmittableThreadLocal | 确保用户身份在线程池异步场景下不丢失 |
| 链路追踪 | @RagTraceRoot + @RagTraceNode 注解驱动 | 树形 Trace 结构，TTL 透传 traceId |
| MQ 事务消息 | DelegatingTransactionListener + 事务回调注册 | 支持半消息、本地事务、回查三阶段 |
| SSE 流式推送 | SseEmitterSender（CAS 保证线程安全） | 幂等的 complete / fail 操作 |

### 4.2 Infra-AI — AI 模型基础设施层

Infra-AI 模块对上层业务提供统一的 AI 能力接口，对下层屏蔽不同模型供应商的协议差异，并实现模型路由、熔断降级和首包探测。

```
┌────────────────────────────────────────────────────────────┐
│                   业务层调用入口                             │
│              LLMService / EmbeddingService / RerankService │
└──────────────────────┬─────────────────────────────────────┘
                       │
┌──────────────────────▼─────────────────────────────────────┐
│                  路由层 (Routing*Service)                    │
│  ┌─────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │ModelSelector │  │ModelRoutingExec  │  │ModelHealth   │  │
│  │(候选排序)    │  │(降级迭代)        │  │Store(熔断器) │  │
│  └─────────────┘  └──────────────────┘  └──────────────┘  │
└──────────────────────┬─────────────────────────────────────┘
                       │
┌──────────────────────▼─────────────────────────────────────┐
│               Provider Client 抽象层                        │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  AbstractOpenAIStyleChatClient (模板方法模式)         │  │
│  │  AbstractOpenAIStyleEmbeddingClient                   │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌───────────┐  ┌────────────────┐  ┌──────────────┐      │
│  │ BaiLian   │  │ SiliconFlow    │  │   Ollama     │      │
│  │ ChatClient│  │ ChatClient     │  │  ChatClient  │      │
│  │ EmbClient │  │ EmbClient      │  │  EmbClient   │      │
│  │ RerankClnt│  │                │  │              │      │
│  └───────────┘  └────────────────┘  └──────────────┘      │
└────────────────────────────────────────────────────────────┘
```

#### 4.2.1 三大 AI 能力

| 能力 | 业务接口 | 客户端接口 | 支持的供应商 |
|---|---|---|---|
| Chat（对话） | LLMService | ChatClient | 百炼、SiliconFlow、Ollama |
| Embedding（向量化） | EmbeddingService | EmbeddingClient | SiliconFlow、Ollama |
| Rerank（重排序） | RerankService | RerankClient | 百炼、Noop（测试用） |

#### 4.2.2 模型路由与熔断

模型路由机制确保系统不依赖单一模型供应商，核心流程如下：

```
用户请求
    │
    ▼
ModelSelector.selectCandidates()
    │
    ├─ 按优先级排序候选模型列表
    ├─ 过滤掉熔断状态为 OPEN 的模型
    │
    ▼
ModelRoutingExecutor.executeWithFallback()
    │
    ├─ 候选 1 → 调用 → 成功？ → 返回结果
    │                  失败？ → 标记失败，尝试下一个
    ├─ 候选 2 → 调用 → 成功？ → 返回结果
    │                  失败？ → 标记失败，尝试下一个
    ├─ 候选 3 → 调用 → ...
    │
    ▼
全部失败 → 抛出 RemoteException
```

#### 4.2.3 三态熔断器

```
         失败次数 < 阈值                失败次数 ≥ 阈值
    ┌──────────┐  (正常调用)   ┌──────────┐  (触发熔断)
    │  CLOSED  │──────────────▶│   OPEN   │
    │ (允许调用) │              │ (拒绝调用) │
    └──────────┘               └────┬─────┘
         ▲                          │
         │    探测成功               │ 冷却期结束
         │                          ▼
         │                    ┌───────────┐
         └────────────────────│ HALF_OPEN │
              探测成功         │ (放行1个   │
              恢复CLOSED       │  探测请求) │
                               └───────────┘
                                    │ 探测失败
                                    ▼
                              回到 OPEN 状态
```

#### 4.2.4 流式首包探测

流式对话场景下的首包探测机制，确保模型切换时用户端不会收到半截脏数据：

```
RoutingLLMService.streamChat()
    │
    ├─ 候选 1 → ProbeStreamBridge 包装
    │            │
    │            ├─ 等待首包（最长60秒）
    │            ├─ 首包到达 → 刷新缓冲区，后续直接透传 → 返回
    │            ├─ 首包超时/错误 → 取消流，标记失败 → 尝试下一个
    │
    ├─ 候选 2 → ProbeStreamBridge 包装
    │            └─ ...（同上）
    │
    ▼
全部失败 → callback.onError()
```

### 4.3 Bootstrap — 业务应用层

Bootstrap 是系统的核心业务模块，包含六大业务域：

```
┌─────────────────────────────────────────────────────────────────┐
│                        bootstrap 业务域                          │
├──────────┬──────────┬──────────┬──────────┬──────────┬──────────┤
│  user    │  admin   │knowledge │   rag    │ingestion │   core   │
│ 用户管理  │ 管理后台  │ 知识库    │ RAG问答   │ 入库流水线│ 解析分块  │
├──────────┼──────────┼──────────┼──────────┼──────────┼──────────┤
│ 注册登录  │ 仪表盘   │ 知识库CRUD│ 问题重写  │ 流水线编排│ 文档解析  │
│ 角色权限  │ KPI概览  │ 文档管理  │ 意图识别  │ 节点执行  │ 分块策略  │
│ 密码管理  │ 趋势图表  │ 分块管理  │ 多路检索  │ 条件分支  │ 向量化    │
│          │ 性能指标  │ 定时刷新  │ Prompt   │ 抓取策略  │          │
│          │          │ 文件上传  │ 会话记忆  │          │          │
│          │          │          │ MCP工具   │          │          │
│          │          │          │ 链路追踪  │          │          │
│          │          │          │ 意图引导  │          │          │
│          │          │          │ 限流排队  │          │          │
└──────────┴──────────┴──────────┴──────────┴──────────┴──────────┘
```

### 4.4 MCP-Server — MCP 工具服务

MCP-Server 是一个独立部署的 Spring Boot 应用，通过 JSON-RPC 2.0 over HTTP 协议对外暴露工具能力。

```
┌──────────────────────────────────────────────┐
│              MCP Server (端口 9099)           │
├──────────────────────────────────────────────┤
│  MCPEndpoint (POST /mcp)                     │
│       │                                      │
│       ▼                                      │
│  MCPDispatcher                                │
│       │                                      │
│       ├── initialize → 返回服务器能力声明      │
│       ├── tools/list  → 返回工具列表           │
│       └── tools/call  → 执行具体工具           │
│               │                              │
│               ▼                              │
│  MCPToolRegistry (自动发现所有 MCPToolExecutor)│
│       │                                      │
│       ├── WeatherMCPExecutor (天气查询)       │
│       ├── TicketMCPExecutor  (工单查询)       │
│       └── SalesMCPExecutor   (销售查询)       │
└──────────────────────────────────────────────┘
```

### 4.5 Frontend — 前端工程

前端采用 React 18 + TypeScript + Vite 构建，使用 shadcn/ui（Radix UI）组件库和 Tailwind CSS 进行样式管理。

```
┌──────────────────────────────────────────────────────────────┐
│                      Frontend 架构                            │
├──────────────┬───────────────────────────────────────────────┤
│  路由层       │  React Router v6 + 路由守卫                   │
│              │  RequireAuth / RequireAdmin / RedirectIfAuth   │
├──────────────┼───────────────────────────────────────────────┤
│  页面层       │  登录页 / 聊天页 / 管理后台（14个子页面）       │
│  (pages)     │  仪表盘 / 知识库 / 文档 / 分块 / 意图树 /      │
│              │  入库管理 / 链路追踪 / 系统设置 / 示例问题 /    │
│              │  关键词映射 / 用户管理                          │
├──────────────┼───────────────────────────────────────────────┤
│  状态管理     │  Zustand（3 个 Store）                        │
│  (stores)    │  authStore / chatStore / themeStore            │
├──────────────┼───────────────────────────────────────────────┤
│  API 服务层   │  Axios（REST） + 自定义 SSE 客户端（流式）     │
│  (services)  │  12 个 API 服务模块                            │
├──────────────┼───────────────────────────────────────────────┤
│  组件层       │  ui/（shadcn组件）/ chat/ / layout/ /         │
│  (components)│  common/ / session/ / admin/                  │
└──────────────┴───────────────────────────────────────────────┘
```

---

## 5. 核心业务流程

### 5.1 RAG 问答全链路

一次用户提问在 Ragent 中经过的完整链路：

```
用户提问
  │
  ▼
┌───────────────────────────────────────────────────────────────┐
│ 1. 排队限流                                                    │
│    Redis ZSET + Lua脚本排队 / Semaphore并发控制 / SSE状态推送   │
└───────────────────────┬───────────────────────────────────────┘
                        ▼
┌───────────────────────────────────────────────────────────────┐
│ 2. 问题重写                                                    │
│    多轮对话上下文补全 / 复杂问题拆分为子问题 / 关键词归一化映射  │
└───────────────────────┬───────────────────────────────────────┘
                        ▼
┌───────────────────────────────────────────────────────────────┐
│ 3. 意图分类                                                    │
│    树形意图体系（领域 → 类目 → 话题）                            │
│    LLM 打分 → 置信度过滤 → 歧义检测 → 必要时引导用户澄清        │
│    判定结果：RAG知识检索 or MCP工具调用                          │
└───────┬───────────────────────────────┬───────────────────────┘
        ▼                               ▼
┌───────────────────┐         ┌───────────────────┐
│ 4a. 多路检索       │         │ 4b. MCP工具调用     │
│ (RAG路径)          │         │ (工具路径)          │
│                    │         │                    │
│ ┌───────────────┐ │         │ LLM参数提取         │
│ │意图定向检索    │ │         │     │               │
│ │(指定Collection)│ │         │     ▼               │
│ └───────┬───────┘ │         │ MCPClient调用       │
│ ┌───────┴───────┐ │         │ HTTP → MCP Server   │
│ │全局向量检索    │ │         │     │               │
│ │(全量Collection)│ │         │     ▼               │
│ └───────┬───────┘ │         │ 获取工具执行结果     │
│     并行执行       │         └────────┬──────────┘
│         ▼         │                  │
│ ┌───────────────┐ │                  │
│ │后处理流水线    │ │                  │
│ │去重 → 重排序   │ │                  │
│ └───────┬───────┘ │                  │
└─────────┼─────────┘                  │
          │                            │
          ▼                            ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. Prompt 组装                                               │
│    系统提示词 + 检索上下文 / 工具结果 + 对话历史 + 用户问题    │
└───────────────────────┬─────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. 模型生成（SSE 流式输出）                                   │
│    多候选路由 → 首包探测 → 自动降级 → 流式内容推送             │
│    支持深度思考模式（thinking + content 双通道）               │
└───────────────────────┬─────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ 7. 后处理                                                    │
│    消息持久化 / 会话记忆更新 / 链路追踪记录 / 用户反馈收集     │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 多路检索引擎

检索是 RAG 系统的核心，Ragent 采用多通道并行 + 后处理流水线的架构：

```
                         重写后的问题
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
     ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
     │ 意图定向检索  │ │ 全局向量检索  │ │  (可扩展...)  │
     │ 通道          │ │ 通道          │ │              │
     │              │ │              │ │              │
     │ 按意图路由到  │ │ 全量 Collection│ │              │
     │ 指定Collection│ │ 并行检索      │ │              │
     └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
            │                │                │
            └────────────────┼────────────────┘
                             ▼
                    ┌─────────────────┐
                    │   结果合并       │
                    └────────┬────────┘
                             ▼
                    ┌─────────────────┐
                    │  去重后处理器     │
                    │ (跨通道去重)     │
                    └────────┬────────┘
                             ▼
                    ┌─────────────────┐
                    │  重排序后处理器   │
                    │ (Rerank模型)    │
                    └────────┬────────┘
                             ▼
                      最终检索结果
```

每个检索通道独立执行、互不影响，通过线程池并行调度。后处理器按顺序串联，逐步精炼检索结果。

### 5.3 意图识别体系

意图识别采用树形多级分类结构，结合 LLM 打分和置信度过滤：

```
                        用户问题
                           │
                           ▼
                  ┌─────────────────┐
                  │ IntentClassifier │
                  │ (LLM 打分)       │
                  └────────┬────────┘
                           │
                           ▼
              对所有叶节点打分 + 置信度排序
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         高置信度       中置信度       低置信度
         (≥阈值)      (歧义区间)     (<阈值)
              │            │            │
              ▼            ▼            ▼
        直接执行      IntentGuidance   全局检索
        指定意图      Service 歧义     (不限定意图)
                      检测与引导
                           │
                    ┌──────┴──────┐
                    ▼             ▼
              引导用户澄清    置信度OK
              (返回选项)     (执行意图)
```

意图树的三级结构：

```
Level 0: DOMAIN（领域）
    ├── Level 1: CATEGORY（类目）
    │       ├── Level 2: TOPIC（话题/叶节点）
    │       ├── Level 2: TOPIC（话题/叶节点）
    │       └── ...
    ├── Level 1: CATEGORY（类目）
    │       └── ...
    └── ...
```

每个叶节点可关联一个知识库 Collection 或一个 MCP 工具 ID，决定该意图走 RAG 检索路径还是 MCP 工具调用路径。

### 5.4 会话记忆管理

```
                    当前对话
                       │
                       ▼
              ┌─────────────────┐
              │ 滑动窗口         │
              │ (保留近 N 轮)    │
              └────────┬────────┘
                       │
              ┌────────┴────────┐
              │  轮数是否超限？   │
              └────────┬────────┘
                  │           │
              未超限         超限
                  │           │
                  ▼           ▼
           直接使用      ┌──────────────┐
           历史          │ 自动摘要压缩  │
                        │ (LLM 生成)   │
                        │ 摘要持久化到  │
                        │ summary 表   │
                        └──────┬───────┘
                               │
                               ▼
                        摘要 + 最近N轮
                        组装为上下文
```

### 5.5 文档入库流水线（Ingestion Pipeline）

文档从上传到可检索，经过一条基于节点编排的 Pipeline：

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ Fetcher  │───▶│ Parser   │───▶│ Enhancer │───▶│ Chunker  │───▶│ Enricher │───▶│ Indexer  │
│ 数据抓取  │    │ 文档解析  │    │ 文档增强  │    │ 文本分块  │    │ 分块丰富  │    │ 向量入库  │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
     │               │               │               │               │               │
     ▼               ▼               ▼               ▼               ▼               ▼
  从数据源         Tika解析        LLM增强         按策略切分       LLM逐块        写入Milvus
  获取原始字节     PDF/Word/       上下文增强       向量化Embedding  提取关键词      或pgvector
  (本地/HTTP/      Markdown等      关键词提取                       生成摘要
   S3/飞书)                        问题生成

     每个节点支持:
     ├── 条件执行（condition_json）
     ├── 输出链式传递（context 上下文）
     └── 独立执行日志（task_node 记录）
```

#### 5.5.1 数据抓取策略

通过策略模式支持多种数据源：

| 策略 | 说明 |
|---|---|
| LocalFileFetcher | 本地文件系统 |
| HttpUrlFetcher | HTTP/HTTPS URL |
| S3Fetcher | S3 兼容对象存储 |
| FeishuFetcher | 飞书文档平台 |

#### 5.5.2 分块策略

| 策略 | 说明 |
|---|---|
| FixedSizeTextChunker | 固定大小分块，支持可配置重叠 |
| StructureAwareTextChunker | 结构感知分块，尊重文档标题和章节 |

#### 5.5.3 Pipeline 引擎

Pipeline 引擎的核心工作机制：

```
PipelineDefinition (数据库配置)
    │
    ├── NodeConfig 1 → NodeConfig 2 → NodeConfig 3 → ...
    │   (链表结构, nextNodeId 串联)
    │
    ▼
IngestionEngine
    │
    ├── 构建 NodeConfig 链表
    ├── 环路检测
    ├── 找到起始节点
    │
    ├── 对每个节点:
    │   ├── ConditionEvaluator 评估条件
    │   ├── 跳过条件不满足的节点
    │   ├── 调用对应 IngestionNode.execute()
    │   ├── 将输出写入 IngestionContext
    │   └── NodeOutputExtractor 记录日志
    │
    └── 返回 IngestionResult
```

---

## 6. 并发与线程模型

### 6.1 专用线程池

系统根据不同工作负载的特征，配置了 8 个独立线程池：

| 线程池 | 用途 | 特点 |
|---|---|---|
| MCP 批量调用线程池 | 并行调用多个 MCP 工具 | 高并发、短任务 |
| RAG 上下文组装线程池 | 组装检索上下文 | 中等并发 |
| 多路检索线程池 | 并行执行多个检索通道 | IO 密集型 |
| 内部检索线程池 | 单个检索通道内部并行检索 | IO 密集型 |
| 意图分类线程池 | LLM 意图打分 | CPU + IO 混合 |
| 记忆摘要线程池 | 会话记忆摘要压缩 | IO 密集型 |
| 模型流式输出线程池 | OkHttp 流式读取 | 长连接、IO 密集型 |
| 对话入口线程池 | 聊天请求总协调 | 综合型 |

所有线程池均使用 `TtlExecutors` 包装，确保 `UserContext` 和 `RagTraceContext` 在异步线程中正确透传。

### 6.2 排队限流机制

```
用户请求
    │
    ▼
┌──────────────┐
│ ZSET 排队     │  ← 请求入队，按时间戳排序
└──────┬───────┘
       ▼
┌──────────────┐
│ Lua 脚本原子  │  ← 判断是否在队头窗口内
│ 判断          │
└──────┬───────┘
       │
  ┌────┴────┐
  ▼         ▼
允许       排队等待
执行         │
             ▼
┌──────────────┐
│ Semaphore    │  ← 控制最大并发数
│ 并发控制      │     许可自动过期（防死锁）
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Pub/Sub 广播  │  ← 跨实例通知
│ 唤醒          │     本地合并通知避免惊群
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ SSE 推送      │  ← 实时推送排队状态
│ 排队状态      │     超时自动踢出
└──────────────┘
```

---

## 7. 数据模型

### 7.1 数据库 ER 关系

系统使用 PostgreSQL 作为关系数据库，共设计 20+ 张业务表，按业务域划分为以下几组：

```
┌─────────────────────────────────────────────────────────────────┐
│                       用户与会话域                                │
├─────────────┬──────────────┬──────────────┬─────────────────────┤
│  t_user     │t_conversation│ t_message    │t_conversation_summary│
│  用户表      │ 会话表        │ 消息表       │ 会话摘要表           │
│             │              │              │                     │
│ id          │ id           │ id           │ id                  │
│ username    │ conversation │ conversation │ conversation_id     │
│ password    │   _id        │   _id        │ user_id             │
│ role        │ user_id      │ user_id      │ last_message_id     │
│ avatar      │ title        │ role         │ content(摘要)        │
│             │ last_time    │ content      │                     │
│             │              │ thinking_    │                     │
│             │              │   content    │                     │
│             │              │ thinking_    │                     │
│             │              │   duration   │                     │
├─────────────┼──────────────┴──────────────┴─────────────────────┤
│t_message_   │  t_sample_question                              │
│feedback     │  示例问题表                                      │
│消息反馈表    │                                                 │
└─────────────┴─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                       知识库域                                    │
├──────────────┬───────────────┬──────────────┬───────────────────┤
│t_knowledge_  │t_knowledge_   │t_knowledge_  │t_knowledge_       │
│  base        │  document     │  chunk       │  document_chunk_  │
│ 知识库表      │ 文档表         │ 分块表       │  log              │
│              │               │              │ 分块日志表         │
│ id           │ id            │ id           │ id                │
│ name         │ kb_id         │ kb_id        │ doc_id            │
│ embedding_   │ doc_name      │ doc_id       │ status            │
│   model      │ enabled       │ chunk_index  │ 各阶段耗时         │
│ collection_  │ chunk_count   │ content      │ chunk_count       │
│   name       │ file_url      │ content_hash │ error_message     │
│              │ file_type     │ char_count   │                   │
│              │ process_mode  │ token_count  │                   │
│              │ chunk_strategy│ enabled      │                   │
│              │ pipeline_id   │              │                   │
├──────────────┴───────────────┴──────────────┴───────────────────┤
│t_knowledge_document_schedule │ t_knowledge_document_schedule_exec│
│ 文档定时刷新任务表            │ 定时刷新执行记录表                 │
└──────────────────────────────┴──────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                     RAG 意图与检索域                              │
├──────────────┬──────────────────────────────────────────────────┤
│ t_intent_node│ t_query_term_mapping                            │
│ 意图树节点表  │ 关键词归一化映射表                                │
│              │                                                 │
│ id           │ id                                              │
│ kb_id        │ domain                                          │
│ intent_code  │ source_term                                     │
│ name         │ target_term                                     │
│ level        │ match_type                                      │
│ parent_code  │ priority                                        │
│ examples     │ enabled                                         │
│ collection_  │                                                 │
│   name       │                                                 │
│ mcp_tool_id  │                                                 │
│ kind(0:RAG   │                                                 │
│   /1:SYSTEM) │                                                 │
│ prompt相关   │                                                 │
├──────────────┼──────────────────────────────────────────────────┤
│t_rag_trace_  │ t_rag_trace_node                                │
│  run         │ Trace 节点记录表                                  │
│ Trace运行表   │                                                 │
│              │ trace_id → trace_id                              │
│ trace_id     │ node_id / parent_node_id / depth                │
│ trace_name   │ node_type / node_name                           │
│ conversation │ class_name / method_name                         │
│   _id        │ status / duration_ms                            │
│ task_id      │ extra_data                                      │
│ status       │                                                 │
│ duration_ms  │                                                 │
└──────────────┴──────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                       入库流水线域                                │
├──────────────┬────────────────────┬────────────┬────────────────┤
│t_ingestion_  │t_ingestion_        │t_ingestion_│t_ingestion_    │
│  pipeline    │  pipeline_node     │  task      │  task_node     │
│ 流水线表      │ 流水线节点表        │ 任务表     │ 任务节点表      │
│              │                    │            │                │
│ id           │ id                 │ id         │ id             │
│ name         │ pipeline_id        │ pipeline_id│ task_id        │
│ description  │ node_id            │ source_    │ pipeline_id    │
│              │ node_type          │   type     │ node_id        │
│              │ next_node_id(链表) │ source_    │ node_type      │
│              │ settings_json      │   location │ status         │
│              │ condition_json     │ status     │ duration_ms    │
│              │                    │ chunk_count│ output_json    │
│              │                    │ logs_json  │                │
│              │                    │ metadata_  │                │
│              │                    │   json     │                │
└──────────────┴────────────────────┴────────────┴────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                       向量存储域                                  │
├─────────────────────────────────────────────────────────────────┤
│ t_knowledge_vector                                              │
│ 知识库向量存储表（pgvector）                                      │
│                                                                 │
│ id          — 分块ID                                             │
│ content     — 分块文本内容                                        │
│ metadata    — 元数据（JSONB）                                     │
│ embedding   — 向量（vector(1536)，HNSW 索引）                     │
│                                                                 │
│ 注：同时支持 Milvus 作为向量数据库引擎                              │
└─────────────────────────────────────────────────────────────────┘
```

### 7.2 ID 生成策略

所有业务表主键采用 Snowflake 分布式 ID 算法，通过 Redis Lua 脚本在应用启动时原子分配 workerId 和 datacenterId，保证全局唯一性和趋势递增。

---

## 8. 设计模式应用

Ragent 中的设计模式均对应具体的工程问题：

| 设计模式 | 应用场景 | 解决的问题 |
|---|---|---|
| 策略模式 | SearchChannel / PostProcessor / MCPToolExecutor / DocumentFetcher / ChunkingStrategy | 检索通道、后处理器、MCP 工具、数据抓取、分块策略可插拔替换 |
| 工厂模式 | IntentTreeFactory / ChunkingStrategyFactory / StreamCallbackFactory | 复杂对象的创建逻辑集中管理 |
| 注册表模式 | MCPToolRegistry / IntentNodeRegistry | 组件自动发现与注册，新增工具零配置 |
| 模板方法 | IngestionNode / AbstractOpenAIStyleChatClient / AbstractOpenAIStyleEmbeddingClient | 统一执行流程，子类只关注核心逻辑差异 |
| 装饰器模式 | ProbeStreamBridge | 在不修改原有 StreamCallback 的前提下增加首包探测能力 |
| 责任链模式 | 后处理器链、模型降级链 | 多个处理步骤按顺序串联，灵活组合 |
| 观察者模式 | StreamCallback | 流式事件的异步通知（onContent / onThinking / onComplete / onError） |
| 外观模式 | RoutingLLMService / RoutingEmbeddingService / RoutingRerankService | 对上层屏蔽路由、降级、熔断的复杂性 |
| AOP | @RagTraceRoot / @RagTraceNode / @ChatRateLimit / @IdempotentSubmit | 链路追踪、限流、幂等逻辑与业务代码解耦 |

---

## 9. 可观测性

### 9.1 全链路追踪

基于 AOP 注解驱动的 Trace 框架，覆盖 RAG 问答的每一个环节：

```
@RagTraceRoot("rag-chat")
  ├── @RagTraceNode("query-rewrite")     — 问题重写耗时
  ├── @RagTraceNode("intent-classify")   — 意图分类耗时
  ├── @RagTraceNode("retrieval")         — 多路检索耗时
  │     ├── @RagTraceNode("channel-1")   — 单通道耗时
  │     └── @RagTraceNode("channel-2")   — 单通道耗时
  ├── @RagTraceNode("rerank")            — 重排序耗时
  ├── @RagTraceNode("prompt-build")      — Prompt 组装耗时
  └── @RagTraceNode("llm-generate")      — LLM 生成耗时
```

每次 Trace 产生两条记录：
- **t_rag_trace_run** — 记录整体运行信息（traceId、状态、总耗时、错误信息）
- **t_rag_trace_node** — 记录每个节点的详细信息（支持树形嵌套、深度、类名、方法名、额外数据）

### 9.2 入库过程追踪

Ingestion Pipeline 的每个任务和节点都有独立的执行记录：
- **t_ingestion_task** — 任务级别的状态、耗时、错误信息
- **t_ingestion_task_node** — 节点级别的状态、耗时、输出、错误信息
- **t_knowledge_document_chunk_log** — 分块过程的各阶段耗时（提取、分块、向量化、持久化）

---

## 10. 扩展点设计

Ragent 的核心模块均预留了扩展点，新增能力无需修改框架代码：

| 扩展能力 | 方式 | 说明 |
|---|---|---|
| 新增检索通道 | 实现 SearchChannel 接口，注册为 Spring Bean | 自动参与多路检索 |
| 新增后处理器 | 实现 SearchResultPostProcessor 接口 | 自动加入处理链 |
| 新增 MCP 工具 | 实现 MCPToolExecutor 接口，加 @Component | 自动被 MCPToolRegistry 发现 |
| 新增入库节点 | 实现 IngestionNode 接口 | 可插入 Pipeline 任意位置 |
| 新增数据抓取策略 | 实现 DocumentFetcher 接口 | 支持新的数据源类型 |
| 新增分块策略 | 实现 ChunkingStrategy 接口 | 支持新的分块算法 |
| 新增模型供应商 | 实现 ChatClient / EmbeddingClient / RerankClient | 配置候选列表即可参与路由 |
| 新增向量数据库 | 实现 VectorStoreService / VectorStoreAdmin | 支持新的向量存储引擎 |

---

## 11. 前端路由与页面结构

### 11.1 路由总览

| 路径 | 守卫 | 页面 | 说明 |
|---|---|---|---|
| / | 无 | HomeRedirect | 自动跳转 |
| /login | RedirectIfAuth | LoginPage | 登录页 |
| /chat | RequireAuth | ChatPage | 聊天主页 |
| /chat/:sessionId | RequireAuth | ChatPage | 指定会话聊天 |
| /admin/dashboard | RequireAdmin | DashboardPage | 仪表盘 |
| /admin/knowledge | RequireAdmin | KnowledgeListPage | 知识库列表 |
| /admin/knowledge/:kbId | RequireAdmin | KnowledgeDocumentsPage | 文档管理 |
| /admin/knowledge/:kbId/docs/:docId | RequireAdmin | KnowledgeChunksPage | 分块管理 |
| /admin/intent-tree | RequireAdmin | IntentTreePage | 意图树可视化 |
| /admin/intent-list | RequireAdmin | IntentListPage | 意图节点列表 |
| /admin/intent-list/:id/edit | RequireAdmin | IntentEditPage | 意图节点编辑 |
| /admin/ingestion | RequireAdmin | IngestionPage | 入库流水线管理 |
| /admin/traces | RequireAdmin | RagTracePage | 链路追踪列表 |
| /admin/traces/:traceId | RequireAdmin | RagTraceDetailPage | 链路追踪详情 |
| /admin/settings | RequireAdmin | SystemSettingsPage | 系统设置 |
| /admin/sample-questions | RequireAdmin | SampleQuestionPage | 示例问题管理 |
| /admin/mappings | RequireAdmin | QueryTermMappingPage | 关键词映射管理 |
| /admin/users | RequireAdmin | UserListPage | 用户管理 |

### 11.2 前端状态管理

| Store | 管理范围 | 持久化 |
|---|---|---|
| authStore | 用户认证状态、token、当前用户信息 | localStorage |
| chatStore | 会话列表、消息、SSE 流式状态、深度思考模式 | 不持久化 |
| themeStore | 深色/浅色主题切换 | localStorage |

---

## 12. 部署架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                           部署架构                                    │
│                                                                      │
│  ┌────────────┐     ┌─────────────────────────────────────────────┐  │
│  │  Nginx     │     │           Spring Boot 主应用                  │  │
│  │  静态资源   │────▶│           (bootstrap 模块)                    │  │
│  │  反向代理   │     │                                              │  │
│  └────────────┘     │  ┌──────────┐ ┌──────────┐ ┌────────────┐  │  │
│                      │  │framework │ │infra-ai  │ │ bootstrap   │  │  │
│                      │  │  通用框架 │ │ AI抽象层  │ │  业务逻辑   │  │  │
│                      │  └──────────┘ └──────────┘ └────────────┘  │  │
│                      └─────────────────────────────────────────────┘  │
│                                                                      │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐     │
│  │ PostgreSQL │  │   Milvus   │  │   Redis    │  │  RustFS    │     │
│  │ 关系数据库  │  │ 向量数据库  │  │ 缓存/限流  │  │ 对象存储    │     │
│  │ + pgvector │  │            │  │ /分布式锁   │  │ (S3兼容)   │     │
│  └────────────┘  └────────────┘  └────────────┘  └────────────┘     │
│                                                                      │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐                     │
│  │ RocketMQ   │  │ MCP Server │  │  Ollama    │                     │
│  │ 消息队列    │  │ (独立进程)  │  │ 本地模型    │                     │
│  └────────────┘  │  端口 9099  │  └────────────┘                     │
│                   └────────────┘                                      │
│                                                                      │
│  ┌──────────────────────────────────────────┐                        │
│  │     外部模型服务                           │                        │
│  │  百炼(阿里云) / SiliconFlow / 其他兼容API  │                        │
│  └──────────────────────────────────────────┘                        │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 13. 关键流程时序

### 13.1 SSE 流式对话时序

```
 用户              Frontend              Bootstrap             Infra-AI           Model Provider
  │                   │                     │                     │                    │
  │  发送问题          │                     │                     │                    │
  │──────────────────▶│                     │                     │                    │
  │                   │  GET /rag/v3/chat   │                     │                    │
  │                   │  (SSE 连接)          │                     │                    │
  │                   │────────────────────▶│                     │                    │
  │                   │                     │  排队限流检查         │                    │
  │                   │                     │  问题重写            │                    │
  │                   │                     │  意图分类            │                    │
  │                   │                     │────────────────────▶│                    │
  │                   │                     │                     │  多路检索           │
  │                   │                     │                     │  Prompt组装         │
  │                   │                     │                     │  首包探测           │
  │                   │                     │                     │───────────────────▶│
  │                   │                     │                     │                    │
  │                   │  SSE: meta事件       │                     │  SSE: content事件  │
  │                   │◀────────────────────│                     │◀───────────────────│
  │                   │                     │                     │                    │
  │  SSE: thinking    │  SSE: message事件    │                     │  SSE: thinking事件 │
  │◀──────────────────│◀────────────────────│                     │◀───────────────────│
  │                   │                     │                     │                    │
  │  SSE: 内容片段    │  SSE: message事件    │                     │  SSE: content事件  │
  │◀──────────────────│◀────────────────────│                     │◀───────────────────│
  │                   │                     │                     │                    │
  │  ...              │  ...                │                     │  ...               │
  │                   │                     │                     │                    │
  │  SSE: 完成        │  SSE: finish事件     │                     │                    │
  │◀──────────────────│◀────────────────────│                     │                    │
  │                   │                     │  持久化消息          │                    │
  │                   │                     │  记录Trace           │                    │
```

### 13.2 文档入库时序

```
 管理员            Frontend            Bootstrap          IngestionEngine       Milvus/pgvector
  │                   │                   │                     │                      │
  │  上传文档          │                   │                     │                      │
  │──────────────────▶│                   │                     │                      │
  │                   │  POST /ingestion  │                     │                      │
  │                   │  /tasks/upload    │                     │                      │
  │                   │──────────────────▶│                     │                      │
  │                   │                   │  创建Task记录         │                      │
  │                   │                   │─────────────────────▶│                      │
  │                   │                   │                     │                      │
  │                   │                   │                     │  FetcherNode         │
  │                   │                   │                     │  (抓取文件)           │
  │                   │                   │                     │                      │
  │                   │                   │                     │  ParserNode          │
  │                   │                   │                     │  (Tika解析)          │
  │                   │                   │                     │                      │
  │                   │                   │                     │  EnhancerNode        │
  │                   │                   │                     │  (LLM增强)           │
  │                   │                   │                     │                      │
  │                   │                   │                     │  ChunkerNode         │
  │                   │                   │                     │  (分块+向量化)        │
  │                   │                   │                     │                      │
  │                   │                   │                     │  EnricherNode        │
  │                   │                   │                     │  (LLM分块丰富)       │
  │                   │                   │                     │                      │
  │                   │                   │                     │  IndexerNode         │
  │                   │                   │                     │─────────────────────▶│
  │                   │                   │                     │  (写入向量)           │
  │                   │                   │                     │◀─────────────────────│
  │                   │                   │                     │                      │
  │                   │                   │  更新Task状态        │                      │
  │                   │                   │◀─────────────────────│                      │
  │                   │  返回结果          │                     │                      │
  │                   │◀──────────────────│                     │                      │
  │  入库完成          │                   │                     │                      │
  │◀──────────────────│                   │                     │                      │
```

---

## 14. 总结

Ragent 的架构设计围绕以下核心原则展开：

- **分层解耦**：framework / infra-ai / bootstrap 三层各司其职，换模型供应商不用改业务代码，换业务逻辑不用动基础设施
- **接口驱动**：核心能力（检索通道、后处理器、MCP 工具、入库节点、模型客户端）全部面向接口编程，新增能力只需加实现类
- **容错优先**：模型路由 + 熔断降级 + 首包探测 + 排队限流，确保单个组件故障不影响整体服务
- **可观测性**：全链路 Trace + 入库节点日志 + 仪表盘监控，排查与调优有据可依
- **事件驱动**：RocketMQ 解耦异步处理（文档分块、消息反馈），提升系统吞吐和响应速度
