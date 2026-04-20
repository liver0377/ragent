# Ragent Python 版 — 前端架构

## 1. 概述

前端是一个 **单页应用（SPA）**，基于 React + TypeScript 构建，通过 Nginx 反向代理对接后端 FastAPI 服务。整体架构遵循"薄前端"原则——前端只负责 UI 渲染和路由控制，业务逻辑全部在后端完成。

---

## 2. 技术栈

- **框架** — React 19 + TypeScript 6
- **构建工具** — Vite 8（HMR 开发服务器 + 生产打包）
- **UI 组件库** — Ant Design 6（含 @ant-design/icons 图标库）
- **路由** — React Router DOM 7（嵌套路由 + 路由守卫）
- **HTTP 客户端** — Axios 1.x（请求/响应拦截器 + Bearer Token 注入）
- **日期处理** — dayjs
- **静态服务器** — Nginx 1.25-alpine（SPA 路由兜底 + API 反向代理）

---

## 3. 项目结构

```
frontend/
├── public/                     # 静态资源（Vite 直接复制）
├── src/
│   ├── main.tsx                # 入口：挂载 <App /> 到 #root
│   ├── App.tsx                 # 根组件：路由表 + 全局 Provider
│   ├── index.css               # 全局样式（重置 / 滚动条 / 思考动画）
│   │
│   ├── api/                    # API 模块层
│   │   ├── client.ts           # Axios 实例（拦截器 / Bearer Token / 401 跳转）
│   │   ├── auth.ts             # 登录 / 注册 / 获取用户信息
│   │   ├── chat.ts             # SSE 流式聊天（原生 fetch + ReadableStream）
│   │   ├── knowledgeBase.ts    # 知识库 CRUD
│   │   ├── document.ts         # 文档上传 / 列表 / 删除 / 任务轮询
│   │   ├── conversation.ts     # 会话管理（创建 / 列表 / 详情 / 删除）
│   │   └── department.ts       # 部门列表
│   │
│   ├── contexts/
│   │   └── AuthContext.tsx      # 认证上下文（全局 user 状态）
│   │
│   ├── components/
│   │   └── RequireAuth.tsx     # 路由守卫（未登录 → /login）
│   │
│   ├── layouts/
│   │   └── MainLayout.tsx      # 主布局（侧边栏 + 顶栏 + 内容区）
│   │
│   ├── pages/
│   │   ├── LoginPage.tsx       # 登录 / 注册页
│   │   ├── KnowledgePage.tsx   # 知识库管理页
│   │   ├── DocumentsPage.tsx   # 知识库文档列表页
│   │   ├── UploadPage.tsx      # 文档上传页
│   │   └── ChatPage.tsx        # 智能问答对话页
│   │
│   └── utils/
│       └── auth.ts             # Token / User 持久化工具（localStorage）
│
├── deploy/
│   └── nginx.conf              # 生产环境 Nginx 配置
├── Dockerfile                  # 多阶段构建（Node 编译 → Nginx 交付）
├── vite.config.ts              # Vite 配置（开发代理 / 插件）
├── tsconfig.json               # TypeScript 配置
└── package.json                # 依赖声明
```

---

## 4. 路由体系

### 4.1 路由表

**公开路由（无需认证）：**

- **/login** — LoginPage，登录 / 注册页面

**受保护路由（RequireAuth 包裹，嵌套在 MainLayout 内）：**

- **/** — 根路径，重定向到 `/knowledge`
- **/knowledge** — KnowledgePage，知识库管理
- **/knowledge/:kbId/documents** — DocumentsPage，指定知识库的文档列表
- **/upload** — UploadPage，文档上传
- **/chat** — ChatPage，智能问答对话

**兜底路由：**

- **\*** — 所有未匹配路径，重定向到 `/`

### 4.2 路由层级关系

```
App
├── <ConfigProvider locale={zhCN}>        ← Ant Design 中文本地化
├── <BrowserRouter>
└── <AuthProvider>                        ← 全局认证状态
    ├── /login → <LoginPage />            ← 公开路由
    └── / → <RequireAuth>                 ← 路由守卫
            └── <MainLayout>              ← 侧边栏 + 顶栏
                ├── <Outlet>
                │   ├── index     → Navigate /knowledge
                │   ├── knowledge → <KnowledgePage />
                │   ├── knowledge/:kbId/documents → <DocumentsPage />
                │   ├── upload     → <UploadPage />
                │   └── chat       → <ChatPage />
```

---

## 5. 认证流程

### 5.1 登录流程

```
用户输入用户名 + 密码
  │
  ▼
LoginPage → apiLogin() → POST /api/v1/auth/login
  │
  ▼
后端返回 { access_token, user, token_type }
  │
  ▼
AuthContext.login(token, user)
  ├── setToken(token)    → localStorage['access_token']
  ├── setUser(user)      → localStorage['user']（JSON 序列化）
  └── setUserState(user) → React 状态更新
  │
  ▼
navigate('/knowledge')
```

### 5.2 Token 持久化

**存储位置** — localStorage

- `access_token` — JWT Token 字符串
- `user` — 用户信息 JSON（id / username / role / avatar）

**工具函数**（`utils/auth.ts`）：

- `getToken()` / `setToken()` / `removeToken()` — Token 读写
- `getUser()` / `setUser()` / `removeUser()` — 用户信息读写
- `isAuthenticated()` — 检查 Token 是否存在
- `logout()` — 清除 Token + 用户信息

### 5.3 请求鉴权

**Axios 请求拦截器**（`api/client.ts`）：

- 每次请求自动从 localStorage 读取 `access_token`
- 注入 `Authorization: Bearer {token}` 请求头
- 基础 URL 通过环境变量 `VITE_API_BASE` 配置，默认 `/api/v1`
- 超时时间 30 秒

**Axios 响应拦截器**：

- 解包后端 `Result` 格式（`{ code, message, data, timestamp }`）
- `code !== 0` 时拒绝 Promise，抛出 `message` 错误
- **401 自动处理** — 清除本地 Token 和用户信息，跳转 `/login`

### 5.4 路由守卫

**RequireAuth** 组件包裹所有需认证的路由：

- **加载中** — 显示全屏"加载中..."（AuthContext 初始化阶段）
- **未登录（user === null）** — 重定向到 `/login`
- **已登录** — 渲染子组件

**AuthContext 初始化**：

- 应用启动时，若 localStorage 存在 Token，调用 `GET /api/v1/auth/me` 刷新用户信息
- 请求失败则清除 Token（可能已过期），置为未登录状态

---

## 6. 主布局

**MainLayout** 提供全局布局骨架，采用 Ant Design 的 `Layout` 组件：

**侧边栏（Sider）**：

- 固定定位，宽度 220px，可折叠至 80px
- Logo 区域：折叠显示 "R"，展开显示 "RAgent"
- 导航菜单：
  - 📦 知识库（/knowledge）
  - ⬆️ 文档上传（/upload）
  - 💬 智能问答（/chat）
- 子路由 `/knowledge/:kbId/documents` 自动高亮"知识库"菜单项

**顶栏（Header）**：

- 左侧：折叠/展开按钮
- 右侧：用户头像 + 用户名下拉菜单（显示用户名 / 退出登录）
- 吸顶固定（sticky）

**内容区（Content）**：

- 通过 `<Outlet />` 渲染子路由页面
- 内边距 24px

---

## 7. 页面详解

### 7.1 LoginPage — 登录 / 注册

- 双 Tab 切换：登录 / 注册
- **登录表单** — 用户名（必填） + 密码（必填）
- **注册表单** — 用户名（3~32 字符） + 密码（≥6 字符）
- 登录/注册成功后自动存入 Token，跳转 `/knowledge`
- 居中卡片布局，渐变背景（紫色系）

### 7.2 KnowledgePage — 知识库管理

- **列表展示** — Table 分页展示知识库（名称 / 描述 / 所属部门 / 文档数 / 创建时间）
- **创建知识库** — Modal 弹窗表单（名称必填，≤100 字符；描述可选，≤500 字符）
- **删除知识库** — Popconfirm 确认后调用 DELETE 接口
- **查看文档** — 跳转到 `/knowledge/:kbId/documents`
- **上传文档** — 在列表行内点击"上传"打开上传 Modal，支持拖拽和多文件选择
  - 支持 PDF / TXT / MD / DOCX / CSV / JSON / HTML
  - 上传后自动轮询 Celery 任务状态，展示进度条（PENDING → PROCESSING → COMPLETED / FAILURE）
- **部门映射** — 启动时加载部门列表，将 `department_id` 映射为部门名称显示

### 7.3 DocumentsPage — 知识库文档列表

- **路由参数** — 通过 `useParams()` 获取 `kbId`
- **列表展示** — Table 分页展示文档（文档名称 / 文件类型 / 启用状态 / 分块数 / 处理方式 / 创建时间）
- **删除文档** — Popconfirm 确认后调用 DELETE 接口
- **返回按钮** — 标题栏左侧返回知识库列表

### 7.4 UploadPage — 文档上传

- **知识库选择** — Select 下拉框选择目标知识库
- **拖拽上传区域** — 支持点击选择或拖拽文件
  - 格式过滤：PDF / TXT / MD / DOCX / CSV / JSON / HTML
  - 文件去重（按 name + size 判断）
  - 已选文件列表可逐个移除
- **上传记录** — 实时展示每个文件的处理状态和进度条
  - 并行轮询所有任务的 Celery 状态
  - 状态映射：PENDING / PROCESSING（进度条）/ COMPLETED / FAILURE

### 7.5 ChatPage — 智能问答

**左右分栏布局：**

- **左侧面板** — 会话列表（260px 宽）
  - "新建对话"按钮
  - 会话列表，点击切换，高亮当前会话
  - 每个会话可删除（Popconfirm 确认）
  - 显示会话标题和最后活跃时间

- **右侧面板** — 聊天主区域
  - 标题栏：当前会话名称 + 知识库选择（Select，可选） + 清空对话按钮
  - 消息列表：
    - 用户消息（蓝色背景，右对齐）
    - 助手回复（绿色背景，左对齐）
    - 流式输出时实时追加 token，未完成显示"思考中..."
    - 自动滚动到底部
  - 输入区：Input + 发送按钮，回车发送，发送中禁用输入

**SSE 流式聊天机制**（`api/chat.ts`）：

- 使用原生 `fetch` API（非 Axios），支持 `ReadableStream` 逐块读取
- 请求携带 `Authorization: Bearer {token}`
- 逐行解析 SSE 协议（`data: ...` 格式）
- `data: [DONE]` 标记流结束
- 每个 JSON 数据包中的 `content` 字段作为 token 回调
- 错误包（`type: 'error'`）触发错误回调
- 返回 `AbortController`，支持中断流式请求

---

## 8. API 模块

### 8.1 client.ts — HTTP 客户端

- 基于 `axios.create()` 创建实例，baseURL = `/api/v1`（可通过 `VITE_API_BASE` 覆盖）
- **请求拦截器** — 从 `localStorage` 读取 Token，注入 `Authorization` 头
- **响应拦截器** — 解包 `Result` 格式，code !== 0 抛错；401 状态码自动清除凭据并跳转 `/login`
- 超时 30 秒，Content-Type 默认 `application/json`

### 8.2 auth.ts — 认证接口

- `POST /auth/login` — 用户登录，返回 `{ access_token, user, token_type }`
- `POST /auth/register` — 用户注册，返回同上
- `GET /auth/me` — 获取当前用户信息

### 8.3 chat.ts — SSE 流式聊天

- `POST /api/v1/chat` — 流式对话（原生 fetch + ReadableStream）
- 请求参数：`question`（必填）、`conversation_id`（可选）、`knowledge_base_id`（可选）
- 返回 `AbortController` 用于取消请求

### 8.4 knowledgeBase.ts — 知识库 CRUD

- `GET /knowledge-bases` — 分页列表（page / page_size）
- `GET /knowledge-bases/:id` — 知识库详情
- `POST /knowledge-bases` — 创建知识库
- `DELETE /knowledge-bases/:id` — 删除知识库

### 8.5 document.ts — 文档管理

- `POST /documents/upload` — 批量上传（multipart/form-data），返回上传结果列表
- `GET /knowledge-bases/:kbId/documents` — 文档分页列表
- `DELETE /documents/:docId` — 删除文档
- `GET /ingestion/tasks/:taskId` — 查询摄入任务状态
- `pollTaskUntilDone()` — 轮询辅助函数，默认每 2 秒查询一次，最多 60 次（2 分钟超时）

### 8.6 conversation.ts — 会话管理

- `POST /conversations` — 创建会话
- `GET /conversations` — 会话分页列表
- `GET /conversations/:id` — 会话详情（含消息列表）
- `DELETE /conversations/:id` — 删除会话

### 8.7 department.ts — 部门

- `GET /departments` — 获取所有部门列表

---

## 9. 构建与部署

### 9.1 开发环境

**Vite 开发服务器**（`vite.config.ts`）：

- 端口 5173，监听 `0.0.0.0`
- API 代理：`/api` → `http://localhost:8000`（自动 changeOrigin）
- 启用 `@vitejs/plugin-react` 插件

**启动命令**：

- `npm run dev` — 启动开发服务器
- `npm run build` — TypeScript 编译 + Vite 生产构建（输出到 `dist/`）
- `npm run preview` — 预览生产构建

### 9.2 生产构建

**Dockerfile 多阶段构建**：

- **阶段 1（builder）** — `node:20-alpine`
  - `npm ci` 安装依赖
  - `npm run build` 编译 TypeScript 并打包
- **阶段 2（serve）** — `nginx:1.25-alpine`
  - 将构建产物 `dist/` 复制到 `/usr/share/nginx/html`
  - 复制 `deploy/nginx.conf` 为 Nginx 配置
  - 暴露 80 端口

### 9.3 Nginx 配置（`deploy/nginx.conf`）

**核心规则：**

- **上传限制** — `client_max_body_size 100m`
- **Gzip 压缩** — 开启，压缩 JSON / JS / CSS / XML 等类型，最小 1024 字节
- **API 反向代理** — `/api/` → `http://ragent-api:8000`
  - 关闭 `proxy_buffering` 和 `proxy_cache`（支持 SSE 流式推送）
  - 读/写超时 300 秒（长连接支持）
  - 透传真实 IP 和协议头
- **SPA 路由兜底** — `try_files $uri $uri/ /index.html`
- **静态资源缓存** — `/assets/` 目录设置 30 天强缓存（immutable）

---

## 10. 前后端交互总览

```
浏览器
  │
  ├─ 静态资源 ────── Nginx (try_files) ─── /usr/share/nginx/html
  │
  └─ /api/v1/* ───── Nginx (proxy_pass) ─── ragent-api:8000
                       │
                       ├── /auth/*       ← 认证（JWT 签发 / 验证）
                       ├── /chat         ← SSE 流式问答
                       ├── /knowledge-bases/*  ← 知识库 CRUD
                       ├── /documents/*  ← 文档管理 + 上传
                       ├── /conversations/*  ← 会话管理
                       └── /departments  ← 部门列表
```

**关键交互模式：**

- **常规请求** — Axios 自动附加 Bearer Token → 后端 JWT 中间件校验 → 返回 `Result<T>` 格式
- **流式请求** — 原生 fetch 发起 POST → 后端 `StreamingResponse` → SSE 协议逐 token 推送 → 前端 ReadableStream 实时解析
- **文件上传** — `multipart/form-data` 提交 → 后端接收 + Celery 异步入库 → 前端轮询任务状态
- **认证失效** — 任意请求返回 401 → Axios 拦截器清除凭据 → 跳转 `/login`
