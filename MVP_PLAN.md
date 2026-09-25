# InsightBot MVP 计划

> 来源：resume_ai.pdf 中的项目描述
> - 用 LangChain + FastAPI 做一个对话式 AI 数据分析 API，用自然语言查询 PostgreSQL 里的结构化数据，支持流式响应
> - LangChain agent 带自定义工具（SQL 生成、数据聚合），用 Redis 存多轮对话记忆，用 Docker 容器化
>
> 技术栈：Python, LangChain, FastAPI, OpenAI API, PostgreSQL, Redis, Docker

---

## 1. MVP 目标

一句话：**用户用中文或英文问一个业务问题，系统自己查数据库，返回有数字依据的答案，并且能接着追问。**

示例对话：
```
用户: 2024 年每个月的销售额是多少？
Bot:  （调用工具 → 生成 SQL → 执行）2024 年总销售额 $X，最高是 12 月 $Y……
用户: 那 Alberta 的客户占多少？          ← 多轮，依赖上一轮上下文
Bot:  ……
```

### 成功标准（验收）
| # | 标准 | 怎么验证 |
|---|------|----------|
| 1 | 10 个预设业务问题中至少 8 个答对 | 人工对照手写 SQL 的结果 |
| 2 | 同一 session 追问能用上前文 | "那上个月呢？"这类问题能答对 |
| 3 | 3 秒内显示请求已接收；分别记录首次工具事件、首个答案 token 和总耗时 | 浏览器 + evaluation.run 报告 |
| 4 | 任何写操作（DELETE/DROP/UPDATE…）都执行不了 | 单元测试 + 手动 prompt injection 测试 |
| 5 | `docker compose up` 一条命令启动全部服务 | 在干净机器上跑一遍 |

---

## 2. 范围

### 做（In scope）
- 一个示例数据库（电商：customers / products / orders / order_items，几千行假数据）
- LangChain agent + 4 个自定义工具
- FastAPI：普通 `/chat` 和流式 `/chat/stream`（SSE）
- Redis 存会话历史（按 session_id，滑动窗口 + 过期时间）
- SQL 安全：只读数据库账号 + SQL 语法树校验 + 自动加 LIMIT + 查询超时
- Docker Compose（api + postgres + redis）
- 一个极简网页用来演示（可选，半天以内）
- 基础测试（SQL 校验、工具、API）

### 不做（Out of scope，写进"后续计划"）
- 用户登录 / 权限 / 多租户
- 用户上传自己的数据库或 CSV
- 图表生成
- 精细的 prompt 评测体系、成本监控
- 生产部署（K8s、CI/CD）

---

## 3. 架构

```
浏览器 / curl
     │  POST /chat/stream  {session_id, question}
     ▼
┌──────────────── FastAPI ────────────────┐
│  1. 从 Redis 取该 session 的历史消息      │
│  2. 调 LangChain Agent（OpenAI 模型）     │
│        │  循环：思考 → 调工具 → 看结果     │
│        ├── list_tables                    │
│        ├── describe_tables                │
│        ├── run_sql_query ──► SQL 校验 ──► PostgreSQL（只读账号）
│        └── aggregate     ──► 拼安全 SQL ─┘
│  3. 边生成边通过 SSE 推给前端             │
│  4. 把 (问题, 最终答案) 写回 Redis        │
└──────────────────────────────────────────┘
```

### 模块划分
| 模块 | 职责 |
|------|------|
| `config` | 读取环境变量（API key、数据库地址、Redis 地址、行数上限等） |
| `db` | 数据库连接、读表结构、执行查询（只读事务 + 超时） |
| `sql_guard` | 校验 LLM 生成的 SQL：只允许单条 SELECT，禁止危险函数，强制 LIMIT |
| `tools` | 4 个 LangChain 工具 |
| `memory` | Redis 会话历史（另有内存版方便本地开发/测试） |
| `agent` | 组装 agent、system prompt、普通调用和流式调用 |
| `main` | FastAPI 路由 |

---

## 4. 关键设计

### 4.1 Agent 工具
| 工具 | 输入 | 作用 |
|------|------|------|
| `list_tables` | 无 | 列出所有表和字段，agent 第一步先看有什么数据 |
| `describe_tables` | 表名列表 | 字段类型、注释、3 行样例数据，帮 LLM 写对 SQL |
| `run_sql_query` | 一条 SQL | 执行 LLM 自己写的 SELECT（支持 join、子查询）|
| `aggregate` | 表、聚合函数、字段、分组、时间粒度 | 单表常见聚合不用写 SQL，参数化拼接，更稳 |

工具出错时**返回错误文本而不是抛异常**，让 agent 看到报错后自己改 SQL 重试。

### 4.2 SQL 安全（三层防护）
1. **数据库层**：API 用只读账号 `insight_reader`，只有 SELECT 权限，默认只读事务，5 秒超时
2. **应用层**：用 sqlglot 把 SQL 解析成语法树检查（不是正则），拦截：多条语句、INSERT/UPDATE/DELETE/DROP、`SELECT INTO`、`FOR UPDATE`、`pg_sleep` 等危险函数、CTE 里藏的 DELETE
3. **结果层**：自动加 / 收紧 LIMIT（默认 200 行），返回给 LLM 的内容截断到 8000 字符，控制 token 成本

### 4.3 多轮记忆
- Redis key：`insightbot:history:{session_id}`，List 结构
- 只存「用户问题 + 最终答案」，不存中间工具调用 → 历史短、省 token，也避免裁剪时留下孤立的 tool 消息
- 保留最近 20 条（`LTRIM`），24 小时不活跃过期（`EXPIRE`）

### 4.4 流式响应
- 用 SSE（Server-Sent Events），每行 `data: {json}`
- 事件类型：`session` → `tool_call`（调了哪个工具、参数）→ `tool_result` → `token`（答案逐字）→ `done`（完整答案 + 用到的 SQL）/ `error`
- 前端能看到 agent 的"思考过程"（执行了什么 SQL），答案也更可信

### 4.5 API
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/chat` | 一次性返回 `{session_id, answer, sql[]}` |
| POST | `/chat/stream` | SSE 流式 |
| GET | `/sessions/{id}/history` | 查看会话历史 |
| DELETE | `/sessions/{id}` | 清空会话 |
| GET | `/schema` | 查看表结构 |
| GET | `/health` | 健康检查 |

---

## 5. 开发里程碑

| 阶段 | 内容 | 产出 | 预计 |
|------|------|------|------|
| M0 环境 | docker-compose 起 Postgres + Redis；写 `init.sql` 建表、造数据、建只读账号 | 能用 psql 连上看到数据 | 0.5 天 |
| M1 数据层 | `db` + `sql_guard`，先写测试（危险 SQL 全部被拒） | 测试通过 | 0.5 天 |
| M2 工具 + Agent | 4 个工具 + system prompt，命令行里能问答 | 能答对简单问题 | 1 天 |
| M3 API | FastAPI `/chat`，Redis 记忆，多轮追问 | curl 能多轮对话 | 0.5 天 |
| M4 流式 | `/chat/stream` SSE + 极简网页 | 浏览器里逐字显示 | 0.5 天 |
| M5 评测 + 打磨 | 10 个标准问题跑一遍，调 prompt；README；Dockerfile | 满足验收标准 | 1 天 |

**合计约 4 天。**

### 10 个评测问题（示例）
1. 一共有多少客户？
2. 每个省的客户数量？
3. 2024 年每月销售额（只算 completed 订单）？
4. 销量最高的 5 个产品？
5. 哪个品类收入最高？
6. 退款率是多少？
7. 平均客单价是多少？
8. 来自 Edmonton 的客户消费总额？
9. （追问）那 Calgary 呢？
10. （追问）两者差多少百分比？

---

## 6. 风险与应对
| 风险 | 应对 |
|------|------|
| LLM 写错 SQL / 字段名编错 | 先 describe 再写；报错信息回传让它重试；最多 10 步防死循环 |
| LLM 编造数字 | prompt 要求所有数字必须来自工具结果；返回里附上执行过的 SQL 方便核对 |
| SQL 注入 / 删库 | 三层防护（见 4.2） |
| 查询太慢或结果太大 | 超时 5 秒 + LIMIT + 输出截断 |
| token 成本 | 历史只存问答对、结果截断、默认用 gpt-4o-mini |
| 业务口径歧义（"销售额"算不算退款？） | 表注释写清口径；prompt 要求说明假设 |

---

## 7. 后续迭代（MVP 之后）
- 结果自动出图（返回 chart spec）
- 支持上传 CSV / 连接用户自己的数据库
- 用 LangSmith 做 tracing 和评测集回归
- Schema 很大时，用向量检索只挑相关表给 LLM（可以和 DocuQuery 的 FAISS 经验结合）
- 登录鉴权、限流、查询缓存（Redis 缓存相同问题的 SQL 结果）

---

## 8. 面试可讲的点
- **为什么用 agent 而不是一次性 text-to-SQL**：可以先看表结构、出错能自我修正、复杂问题拆多步
- **为什么 SQL 校验用语法树不用正则**：正则会被注释、大小写、CTE 绕过
- **为什么 Redis 只存问答对**：token 成本 + 消息裁剪时的一致性
- **为什么同时有 `aggregate` 和 `run_sql_query`**：常见聚合走参数化工具更稳定，复杂查询才让 LLM 自由写 SQL
- **流式怎么实现**：LangGraph 的 `stream_mode=["messages", "updates"]`，token 和工具事件分开推送


## 9. Implementation update — 2026-09-22

The backend reliability fixes, browser demo, setup documentation, integration checks,
and bilingual evaluation tooling are implemented. Query SQL is preserved in tool
artifacts even when model-facing output is truncated. `/ready` checks PostgreSQL
and history availability; `/health` remains liveness. The browser uses POST SSE,
shows request acceptance and tool activity, and supports session reset.

The accepted latency target is request-accepted progress within three seconds,
not first answer token within three seconds. The evaluation records both timings
separately. Business definitions and the ten bilingual questions with reference SQL
are in `evaluation/cases.json` and README.md.

Verification status and outstanding acceptance gates are in `ACCEPTANCE.md`.
The MVP must not be labelled fully accepted until real PostgreSQL/Redis, fresh
Compose startup, and live bilingual accuracy checks have passed.
