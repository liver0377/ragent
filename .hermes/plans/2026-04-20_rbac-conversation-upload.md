# RAgent 企业级改造实施计划

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 将 RAgent 改造为企业内部知识库平台，实现 RBAC 部门隔离、会话持久化、真实文件上传

**Architecture:** User 加 department_id → KnowledgeBase 加 department_id → 部门级隔离。会话从内存字典迁移到 PostgreSQL（已有表结构）。文件上传从"填文件名"改为 multipart/form-data 真实上传。

**Tech Stack:** FastAPI + SQLAlchemy async + PostgreSQL + React 19 + Antd 6 + TypeScript

---

## Phase 1: 后端 — RBAC 部门模型

### Task 1.1: 添加 Department 模型 + User/KB 关联

**Objective:** 创建部门表，User 和 KnowledgeBase 加 department_id 字段

**Files:**
- Modify: `src/ragent/common/models.py`
- Create: `src/ragent/db/migrations/add_department.py`（Alembic 迁移脚本或手动 SQL）

**Changes:**
1. 新增 `Department` 模型（id, name, created_at, updated_at）
2. `User` 加 `department_id` 外键
3. `KnowledgeBase` 加 `department_id` 外键
4. 数据库 ALTER TABLE 添加列 + 创建测试部门

---

## Phase 2: 后端 — 会话持久化

### Task 2.1: 改造 SessionMemoryManager 为 DB 持久化

**Objective:** 会话消息写入 t_conversation/t_message 表，而非内存字典

**Files:**
- Modify: `src/ragent/rag/memory/session_memory.py`
- Modify: `src/ragent/rag/chain.py`

**Changes:**
1. SessionMemoryManager 接收 async db session
2. `add_message()` 写入 `Message` 表 + 更新 `Conversation.last_message_time`
3. `get_memory()` 从 `Message` 表读取，而非内存字典
4. `summarize()` 写入 `ConversationSummary` 表
5. 新会话时自动创建 `Conversation` 记录

### Task 2.2: Chat 接口加鉴权 + 会话 CRUD API

**Objective:** /api/v1/chat 加 CurrentUser，新增会话列表/详情/删除接口

**Files:**
- Modify: `src/ragent/app/router.py`（chat 端点加 CurrentUser）
- Add: conversation 相关 API 端点（list/create/get/delete）

**Changes:**
1. `chat()` 加 `current_user: CurrentUser` 参数，user_id 从 JWT 取
2. 新增 `POST /api/v1/conversations` — 创建会话
3. 新增 `GET /api/v1/conversations` — 用户会话列表（按 user_id 过滤）
4. 新增 `GET /api/v1/conversations/{id}/messages` — 会话消息历史
5. 新增 `DELETE /api/v1/conversations/{id}` — 删除会话

---

## Phase 3: 后端 — 文件上传改造

### Task 3.1: 文档上传改为 multipart/form-data

**Objective:** 支持真实文件上传（拖拽/选择），支持批量上传

**Files:**
- Modify: `src/ragent/app/router.py`（upload 端点）
- Modify: `src/ragent/config/settings.py`（添加 UPLOAD_DIR 配置）

**Changes:**
1. `/api/v1/documents/upload` 改为接收 `UploadFile`（FastAPI multipart）
2. 文件保存到 `UPLOAD_DIR`（默认 `/data/uploads/`）
3. 新增 `POST /api/v1/documents/upload-batch` 批量上传接口
4. 文件类型白名单校验（.pdf, .txt, .md, .docx 等）

---

## Phase 4: 前端 — 会话管理 UI

### Task 4.1: ChatPage 添加会话列表侧边栏

**Objective:** 左侧显示历史会话列表，支持新建/切换/删除会话

**Files:**
- Modify: `frontend/src/pages/ChatPage.tsx`
- Add: `frontend/src/api/conversation.ts`

**Changes:**
1. 新建 `api/conversation.ts` — 会话 API（list/create/get/delete）
2. ChatPage 左侧加会话列表（Antd List/Sider）
3. 点击会话加载历史消息
4. "新建对话"按钮
5. SSE 流完成后自动保存消息到前端状态

---

## Phase 5: 前端 — 文件上传 UI 改造

### Task 5.1: UploadPage 改为拖拽/选择上传 + 批量

**Objective:** 替换"输入文件名"为 Antd Upload.Dragger 组件

**Files:**
- Modify: `frontend/src/pages/UploadPage.tsx`
- Modify: `frontend/src/api/document.ts`

**Changes:**
1. 替换 Input 为 `Upload.Dragger`（Antd 拖拽上传组件）
2. API 改为 `FormData` + `multipart/form-data`
3. 支持多文件批量上传
4. 上传进度条显示每个文件状态
5. 知识库下拉仅显示当前部门的知识库

---

## Phase 6: 前端 — 知识库部门隔离

### Task 6.1: 知识库按部门过滤 + 管理员全局可见

**Objective:** 普通用户只能看到本部门知识库，admin 可见全部

**Files:**
- Modify: `frontend/src/pages/KnowledgePage.tsx`
- Modify: `frontend/src/api/knowledgeBase.ts`

**Changes:**
1. 后端 list_knowledge_bases 按 department_id 过滤
2. admin 角色跳过过滤（可见全部）
3. 前端 KnowledgeBase 类型加 department_id/department_name
4. 知识库列表显示所属部门标签（admin 视图）

---

## 执行顺序

1. **Task 1.1** → 数据库改表（Department + 外键）
2. **Task 2.1** → 会话持久化（SessionMemoryManager → DB）
3. **Task 2.2** → Chat 鉴权 + 会话 API
4. **Task 3.1** → 文件上传改造
5. **Task 4.1** → 前端会话 UI
6. **Task 5.1** → 前端上传 UI
7. **Task 6.1** → 前端知识库隔离

每个 Task 完成后 rebuild docker 镜像 + 验证。
