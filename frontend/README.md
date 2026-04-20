# RAgent 前端

RAgent 是一个基于 RAG（检索增强生成）的智能问答系统。本目录为前端工程，提供知识库管理、文档上传、智能问答等功能的 Web 界面。

---

## 技术栈

**核心框架：** React 19 + TypeScript 6

**构建工具：** Vite 8（`@vitejs/plugin-react` 插件）

**UI 组件库：** Ant Design 6 + Ant Design Icons 6

**路由：** React Router DOM 7（BrowserRouter 模式）

**HTTP 客户端：** Axios（封装统一请求/响应拦截器）

**日期处理：** Day.js

**代码规范：** ESLint + typescript-eslint + react-hooks / react-refresh 插件

---

## 项目结构

```
frontend/
├── deploy/
│   └── nginx.conf            # Nginx 生产配置
├── Dockerfile                # 多阶段构建镜像
├── nginx.conf                # （未使用，实际配置在 deploy/ 下）
├── package.json
├── vite.config.ts
├── tsconfig.json
├── index.html
└── src/
    ├── main.tsx              # 应用入口
    ├── App.tsx               # 路由配置
    ├── index.css             # 全局样式
    ├── vite-env.d.ts         # Vite 类型声明
    ├── assets/               # 静态资源
    ├── api/                  # API 请求模块
    │   ├── client.ts         # Axios 实例封装
    │   ├── auth.ts           # 认证接口
    │   ├── knowledgeBase.ts  # 知识库接口
    │   ├── document.ts       # 文档接口
    │   ├── chat.ts           # SSE 流式聊天接口
    │   ├── conversation.ts   # 会话管理接口
    │   └── department.ts     # 部门接口
    ├── components/
    │   └── RequireAuth.tsx   # 路由守卫组件
    ├── contexts/
    │   └── AuthContext.tsx    # 全局认证上下文
    ├── layouts/
    │   └── MainLayout.tsx    # 主布局（侧边栏 + 顶栏）
    ├── pages/
    │   ├── LoginPage.tsx     # 登录页
    │   ├── KnowledgePage.tsx # 知识库列表页
    │   ├── DocumentsPage.tsx # 文档管理页
    │   ├── UploadPage.tsx    # 文档上传页
    │   └── ChatPage.tsx      # 智能问答页
    └── utils/
        └── auth.ts           # Token / 用户信息工具函数
```

---

## 页面列表

- **LoginPage** — 用户登录/注册页面（公开路由，无需认证）
- **KnowledgePage** — 知识库列表与管理，支持创建和删除知识库
- **DocumentsPage** — 查看指定知识库下的文档列表，支持删除文档
- **UploadPage** — 文档上传页面，支持批量上传并实时展示处理进度
- **ChatPage** — 智能问答页面，基于 SSE 流式接收回答

---

## 路由配置

应用使用 React Router DOM v7 的 `BrowserRouter` 模式，路由定义在 `src/App.tsx` 中。

**公开路由：**

- `/login` — 登录页

**受保护路由（需要认证，使用 `RequireAuth` 守卫）：**

- `/` — 根路径，自动重定向到 `/knowledge`
- `/knowledge` — 知识库列表
- `/knowledge/:kbId/documents` — 指定知识库的文档列表
- `/upload` — 文档上传
- `/chat` — 智能问答

**兜底路由：** 所有未匹配路径重定向到 `/`

主布局 `MainLayout` 采用 Ant Design `Layout` 组件，包含可折叠侧边栏和固定顶栏，通过 `<Outlet />` 渲染子路由内容。

---

## API 模块

所有 API 请求通过 `src/api/client.ts` 中封装的 Axios 实例统一管理。基础路径默认为 `/api/v1`，可通过环境变量 `VITE_API_BASE` 覆盖。

**client.ts — HTTP 客户端封装**
- 请求拦截器：自动注入 `Authorization: Bearer <token>` 头
- 响应拦截器：解包后端 `Result` 格式（`{ code, message, data }`），`code !== 0` 时抛出错误
- 401 响应自动清除本地凭据并跳转到 `/login`
- 超时时间：30 秒

**auth.ts — 认证模块**
- `POST /auth/register` — 用户注册
- `POST /auth/login` — 用户登录，返回 JWT Token 和用户信息
- `GET /auth/me` — 获取当前用户信息

**knowledgeBase.ts — 知识库模块**
- `GET /knowledge-bases` — 知识库列表（分页）
- `GET /knowledge-bases/:id` — 知识库详情
- `POST /knowledge-bases` — 创建知识库
- `DELETE /knowledge-bases/:id` — 删除知识库

**document.ts — 文档模块**
- `POST /documents/upload` — 批量上传文档（multipart/form-data）
- `GET /knowledge-bases/:kbId/documents` — 文档列表（分页）
- `DELETE /documents/:id` — 删除文档
- `GET /ingestion/tasks/:taskId` — 查询摄入任务状态
- `pollTaskUntilDone()` — 轮询任务直到完成（支持进度回调）

**chat.ts — 聊天模块**
- `POST /chat` — SSE 流式聊天（使用原生 `fetch` + `ReadableStream`，非 Axios）
- 支持传入 `conversation_id` 和 `knowledge_base_id`
- 返回 `AbortController` 可取消流式请求

**conversation.ts — 会话管理模块**
- `POST /conversations` — 创建会话
- `GET /conversations` — 会话列表（分页）
- `GET /conversations/:id` — 会话详情（含消息记录）
- `DELETE /conversations/:id` — 删除会话

**department.ts — 部门模块**
- `GET /departments` — 获取所有部门列表

---

## 认证机制

本系统采用基于 JWT（JSON Web Token）的认证方案，完整流程如下：

**1. 登录/注册：** 用户在 `/login` 页面提交用户名和密码，调用后端 `/auth/login` 或 `/auth/register` 接口，获取 `access_token` 和用户信息。

**2. Token 存储：** 登录成功后，Token 和用户信息存储在 `localStorage` 中（键名分别为 `access_token` 和 `user`），由 `src/utils/auth.ts` 工具函数管理。

**3. 请求鉴权：** Axios 请求拦截器自动从 `localStorage` 读取 Token 并注入 `Authorization: Bearer <token>` 请求头。

**4. 全局状态：** `src/contexts/AuthContext.tsx` 提供 `AuthProvider`，在应用初始化时检查 Token 有效性并调用 `GET /auth/me` 刷新用户信息。

**5. 路由守卫：** `src/components/RequireAuth.tsx` 包裹所有受保护路由，未登录用户自动重定向到 `/login`，加载中显示全屏等待状态。

**6. Token 失效处理：** 当后端返回 401 状态码时，响应拦截器自动清除本地存储并跳转到登录页。

---

## 构建与部署

### 本地开发

```bash
# 安装依赖
npm install

# 启动开发服务器（端口 5173）
npm run dev
```

开发模式下，`vite.config.ts` 配置了 `/api` 请求代理到 `http://localhost:8000`，前端自动将 API 请求转发到后端服务。

### 生产构建

```bash
# 类型检查 + 构建产物
npm run build
```

构建产物输出到 `dist/` 目录。

### Docker 部署

前端使用多阶段 Docker 构建，`Dockerfile` 定义如下：

**阶段一（构建）：** 基于 `node:20-alpine`，安装依赖并执行 `npm run build`

**阶段二（运行）：** 基于 `nginx:1.25-alpine`，将构建产物复制到 Nginx 静态资源目录

Nginx 配置要点（`deploy/nginx.conf`）：

- **API 反向代理：** `/api/` 路径代理到 `http://ragent-api:8000`
- **SSE 支持：** 对 API 代理关闭缓冲（`proxy_buffering off`），超时时间 300 秒
- **SPA 路由兜底：** `try_files $uri $uri/ /index.html`，确保前端路由正常工作
- **静态资源缓存：** `/assets/` 下文件缓存 30 天
- **上传限制：** `client_max_body_size 100m`
- **Gzip 压缩：** 启用文本、CSS、JSON、JS 等内容压缩

```bash
# 构建镜像
docker build -t ragent-frontend .

# 运行容器（端口 80）
docker run -p 80:80 ragent-frontend
```
