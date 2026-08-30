# 律所平台 Law Firm Platform

以案件为中心的律所操作系统。架构原则：**极小内核 + 全插件化模块**。

**进度：21 步走完 19 步**（1–18 ＋ 21），另补了归档、引用体检、执业资格
（498 tests passed · 边界检查通过）。

★ 2026-08-28 做过一轮**试用回归** —— 用户第一次从头点到尾，
报回八个问题，**全部真实，一个也没被自动化测试拦住**。
教训记在 DEVELOPMENT_PLAN 的「试用回归」一节，值得先读。

## 先读哪一份

| 我要干什么 | 读这个 |
|---|---|
| **起不来 / 网页打不开** | 本文「每天怎么起」，先跑 `.\dev.ps1 status` |
| 动手做下一步 | [docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md) 对应章节 |
| 改某个已完成模块 | 同上，找到那一步的章节 —— 里面有**为什么这么做**和**已知边界** |
| 写一个新模块 | [docs/MODULE_GUIDE.md](docs/MODULE_GUIDE.md)（三条铁律 + 十二条经验） |
| 动内核 / 跨模块结构 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 手工验收（逐项排查） | [docs/验收清单.html](docs/验收清单.html) |
| **手工验收（一条线走通）** | [十步走查](https://claude.ai/code/artifact/260a8f15-74c5-4527-8c83-6a8e78515a84) —— 跟一个案子从建案走到归档 |
| **看还欠什么** | DEVELOPMENT_PLAN 的「待办：三件已确认要做、但推后的事」 |

★ **改动之前先看那一步的「已知边界」。** 很多看起来像 bug 的东西
是当时明确决定不做的，理由写在那里。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12 + FastAPI + 自建 Registry |
| ORM / 迁移 | SQLAlchemy 2.0 + Alembic（每模块一条独立分支） |
| 校验 / Schema | Pydantic v2（同时产出类型校验与 MCP JSON Schema） |
| 前端 | Next.js 16 + React 19（类型手写在 `lib/api.ts`） |
| 测试 | pytest |
| 编排 | LangGraph + Postgres checkpointer |
| 模型 | DeepSeek V4（`deepseek-v4-flash` / `deepseek-v4-pro`） |
| 数据 | Postgres 16 + pgvector / Redis / MinIO |
| 法律检索 | 独立 HTTP 服务（torch + bge 本地向量） |

## 目录

```
backend/            后端
  kernel/             内核：registry / ports / container / db / web
  modules/            业务模块，每个可独立装卸
  adapters/           外部实现（DeepSeek / MinIO / 检索服务）
  scripts/            工程守护脚本（边界检查）
  tests/
frontend/           前端（Next.js，Step 3 起）
word-addin/         Word 加载项（Step 19）
infra/              docker-compose、种子数据、部署
docs/               开发手册 · 架构 · 模块规范
dev.ps1             本地启停（起全部 / status / stop / backend）
```

## 环境要求

| 项 | 要求 | 当前 |
|---|---|---|
| Python | 3.12+ | 3.12.10 ✅ |
| Node | 22+（前端用） | 22.12.0 ✅ |
| Docker | 起 Postgres/Redis/MinIO | Desktop 4.87.0 ✅ |

## 每天怎么起

```powershell
cd "C:\Users\LF\Desktop\law firm platform"
.\dev.ps1
```

打开 http://localhost:3000 —— 账号 `kangqiao` / `li` / `password123`。

| 命令 | 干什么 |
|---|---|
| `.\dev.ps1` | 起全部（含 Docker） |
| `.\dev.ps1 status` | **网页打不开时先跑这个** |
| `.\dev.ps1 backend` | 只重起后端 |
| `.\dev.ps1 stop` | 停三个应用进程，容器不动 |
| `.\dev.ps1 restart` | 停完再起 |

★ 首次可能报「无法加载文件…未数字签名」。跑一次
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 就好，只用设一次。

### 这套东西是**两层**，两层都得起

```
① Docker 容器    Postgres 5432 · Redis 6379 · MinIO 9000    ← Docker Desktop 里看得到
② 裸跑进程       后端 8000 · 检索 8100 · 前端 3000           ← 看不到
```

**最容易搞混的就是这里**：容器在 Docker Desktop 里看得到，
三个应用进程看不到 —— 它们是普通进程，各占一个命令行窗口。

★ **那三个窗口就是进程本身，不是日志查看器。
关掉窗口 = 杀掉进程，网页立刻打不开。**
容器不受影响（`-d` 是 detached，交给 Docker 守护进程管）。
要停请用 `.\dev.ps1 stop` —— 直接叉窗口偶尔会留下占着端口的僵尸进程，
而那种进程 `taskkill` 会报"找不到"，只能重启电脑。

★ 后端带 `--reload`：**改 Python 代码不用重启**，存盘就生效。
只有改了依赖、或者 reload 卡住时才需要 `.\dev.ps1 backend`。

### 脚本替你多做的三件事（手工最容易漏）

1. **等 Postgres 真的能连** —— `docker compose up -d` 返回 ≠ 数据库能用了，
   中间那几秒后端启动会直接失败退出
2. **跑一遍迁移** —— 见下一节
3. **停的时候按端口精确杀** —— 按进程名杀会连带干掉 :8100 那个 python

### 迁移是什么

代码里给表加一个字段，数据库不会自己知道。中间需要一个脚本：

```python
# modules/identity/migrations/versions/a7c31e9d4b02_lawyer_license.py
op.add_column("user", sa.Column("license_no", ...), schema="identity")
```

`db upgrade` 就是**把还没跑过的迁移按顺序跑一遍**。已经最新就秒过，
所以每次启动跑一遍没有代价；不跑的话，改过表结构之后会看到一堆
「列不存在」的怪错。

★ **不是 Prisma，是 SQLAlchemy + Alembic，全在 Python 里。**
前端的运行时依赖只有 `next / react / react-dom` —— **零数据库能力**，
只通过 HTTP 拿数据。这是刻意的：权限、数据范围、隔离墙全在后端一层。
浏览器里的代码谁都能改，让它碰得到库，那些墙就形同虚设。

★ 每个模块**一条独立的迁移分支**（现在 23 个脚本分在 15 个模块里）——
删掉一个模块，它的建表脚本跟着一起消失。这是「可拆卸」那条铁律的实现方式。

## 第一次装

```bash
cd backend
py -3.12 -m venv .venv
.venv/Scripts/activate          # Windows
pip install -e ".[dev]"

cd ../frontend && npm install
```

之后就一直用 `.\dev.ps1`。

访问 http://127.0.0.1:8000/health 查看内核与模块状态。

## 常用命令

```bash
python -m kernel.cli modules              # 看装载了什么
python -m kernel.cli permissions          # 权限点
python -m kernel.cli capabilities --mcp   # Agent 能力目录 / MCP tools 结构
python -m kernel.cli check                # 边界检查（CI 必跑）
python -m kernel.cli db upgrade
python -m kernel.cli db revision <模块> -m "说明"
pytest
```

## 拆卸测试（本项目的核心验收）

```bash
# 方式一：配置禁用
LFP_DISABLED_MODULES=hello python -m kernel.cli modules

# 方式二：直接删目录
rm -rf backend/modules/hello
```

两种方式系统都必须正常启动。数据库侧一并清除：

```bash
python -m kernel.cli db purge hello     # DROP SCHEMA + 清掉该模块的迁移记录
python -m kernel.cli db upgrade         # 需要时可干净重建
```

## 已完成的模块（16 个）

`identity` 身份权限 · `crm` 客户主体 · `case` 案件 · `conflict` 利冲 ·
`retrieval` 检索 · `citation` 引用留痕 · `workflow` 办案流程 ·
`doc` 案卷 · `draft` 文书 · `task` 时限待办 · `consult` 咨询 ·
`billing` 账单 · `approval` 单据流转 · `archive` 归档 ·
`analytics` 看板 · `hello` 拆卸样板

★ 原来有个 `time`（工时）模块，2026-08-28 **整个拆掉了** ——
中国的提成制律所不按工时给客户开账单。真正按小时收钱的只有法律咨询，
剩下的就是 `consult`。拆卸只改了 billing 一处，
其他 14 个模块一行没动 —— 详见 DEVELOPMENT_PLAN 的 Step 16。

前端页面：工作台 · 案件 · 待办 · 咨询 · 账款 · 单据 · 全所 · 团队 · 客户 · 所务管理

## 开发规范

见 [docs/MODULE_GUIDE.md](docs/MODULE_GUIDE.md)。三条铁律：

1. 业务模块只能 import `kernel.*`，不得 import 任何 `adapters.*`
2. 模块间只能 import 对方的 `api.py`，禁止跨 schema 查表
3. 每个模块自带 `migrations/`，删模块 = 代码与建表脚本一起消失

前两条由 `python -m kernel.cli check` 强制，CI 必跑。
