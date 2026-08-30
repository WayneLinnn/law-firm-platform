# 架构

> 对应到 Step 18 ＋ 21 ＋ 归档（16 个业务模块）。**改架构层面的东西，先读这份；
> 改某一步的细节，读 [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) 对应章节。**

## 分层

```
接入层    Next.js Web  │  Word 插件  │  小程序
业务层    modules/*                        可独立装卸
流程层    WorkflowPort   实现 = LangGraph（Step 9）
契约层    kernel/ports   业务代码只认这里
实现层    adapters/*     DeepSeek、MinIO、法律检索服务
数据层    Postgres（每模块独立 schema）/ Redis / MinIO
```

**流程层与能力层必须分开**：流程是画死的（可审计），能力是模型干的（会犯错）。
模型只在节点内部自由发挥，**永远不决定下一步走哪**。

## 依赖方向（只能向内）

```
modules ──▶ kernel/ports ◀── adapters
              ▲
              └── kernel/container 在启动时把两者接起来
```

业务模块永远不知道背后是 DeepSeek 还是私有化模型，是 LangGraph 还是别的编排引擎。

## 契约（ports）

| Port | 用途 | 实现落地于 |
|---|---|---|
| `ModelPort` | 大模型对话 / 结构化输出 / 向量 | Step 2（DeepSeek） |
| `StoragePort` | 案卷文件 | Step 2（MinIO） |
| `RetrievalPort` | **纯检索**，0 成本、确定性、可缓存 | Step 8（Python RAG 服务） |
| `LegalQAPort` | **问答 / 要素抽取 / 类案对比**，有成本、有 trace、必须留痕 | Step 8（同上） |
| `WorkflowPort` | 案件流程编排、人工中断 | Step 9（LangGraph） |
| `EventPort` | 事件总线 | **预留，暂用空实现 —— 触发条件已满足，见文末** |

`RetrievalPort` 与 `LegalQAPort` 拆开的原因：两者的**计费、缓存、重试策略完全不同**。
纯检索可以随便重跑、可以缓存；问答每次都花钱、必须留痕。

## 两张 LangGraph 不要搞混

| | RAG 检索图（已有，独立进程） | 案件流程图（Step 9，主应用内） |
|---|---|---|
| 管什么 | 一次提问怎么答好 | 一个案件怎么办完 |
| 生命周期 | 秒级 | 数周～数月，跨人接力 |
| 人工确认 | **对平台关闭**（`human=false`） | **只在这一层** |
| 谁调谁 | 被调用方 | 调用方（通过 Port） |

## 数据库

- 每个模块独占一个 Postgres schema，与模块同名（`__table_args__ = schema_of("<name>")`）
- 每个模块自带迁移目录，互不牵连
- 每个模块是一条独立的 Alembic **分支**，升级用 `db upgrade heads`（复数）
- 拆模块：**先** `python -m kernel.cli db purge <name>`（DROP SCHEMA + 清迁移记录），
  **再**删目录

  ★ **顺序不能反。** purge 要读模块的迁移文件才知道该删哪些 revision；
  目录先删了，它就只能 DROP schema、把版本记录留在库里，
  然后下一次 `db upgrade` 报 `Can't locate revision` ——
  **而且是升级时才炸，不是启动时。**

  2026-08-28 拆 `time` 时就是这么踩的。现在顺序反了会**明确报错**
  并给出补救命令（`db purge <name> --revision <rev>`），不再静默留坑。

## 现有模块（16 个）

每个模块独占一个同名 schema。**箭头只表示"要用对方的 api.py"，不是外键。**

| 模块 | schema 里有什么 | 必需依赖 | 可选依赖 |
|---|---|---|---|
| `identity` | 租户、用户、组织、职级、角色、权限 | — | — |
| `crm` | 主体（利冲的基本单位）、客户、联系人、关联方 | identity | — |
| `case` | 案由字典、案件、当事人、承办人、事项、状态流转 | identity, crm | conflict |
| `conflict` | 利冲检查、命中、处置、**隔离墙** | identity, crm, case | — |
| `retrieval` | 检索记录、花费 | identity | case, crm |
| `citation` | 引用快照、句子锚点 | identity | — |
| `workflow` | 案件流程实例（LangGraph checkpoint 由库自建） | identity, case | retrieval, doc, task |
| `doc` | 案卷文档、版本、正文抽取状态 | identity, case | — |
| `draft` | 文书草稿、章节、定稿 | identity, case, retrieval, citation | doc, workflow, crm |
| `task` | 时限规则、案件时限、待办 | identity, case | — |
| `consult` | 法律咨询（按小时收费的那种） | identity, **crm** | case |
| `billing` | 费率、收费约定、账单、账单行、回款、代垫 | identity, case | consult, crm |
| `approval` | 流转规则、单据、单据环节 | identity, case | billing, crm, doc |
| `analytics` | **无表** —— 只汇总，各模块自报指标 | identity | **其余全部**（只问不依赖） |
| `archive` | 归档记录（卷宗目录、指纹、解档历史） | identity, case | **其余全部**（只问不依赖） |
| `hello` | 拆卸测试用的样板模块 | — | — |

★ `consult` 是唯一**不依赖 case** 的业务模块 —— 它挂 `crm`。
多数法律咨询发生在立案之前，甚至根本不会立案；
把 case 写成硬依赖，等于逼着"来聊一下、收 1500、没接案"
这种最典型的情况必须先建个案子。

★ **`time`（工时）曾经在这张表里，2026-08-28 整个拆掉了。**
它做的是英美计时制律所那一套，而中国的提成制律所
团队成员和助理拿的是工资或分成，时间不换算成客户账单。
真正按小时收钱的只有咨询 —— 剩下的就是 `consult`。
详见 DEVELOPMENT_PLAN 的 Step 16。

**依赖只向下游走**：identity ← crm ← case ← 其余。没有环。
`kernel.cli check` 拦非法 import，`kernel.cli modules` 看拓扑排序结果。

### 三种跨模块调用，含义不同

```python
registry.api("case")            # 必需：对方不在，本模块起不来
registry.optional_api("task")   # 可选：对方不在，降级运行（见下）
container.get("model")          # 外部能力：走 Port，不知道背后是谁
```

「可选」不是"随便"，每一处都对应一个**明确的降级行为**，例子：

| 谁调谁 | 对方不在时 |
|---|---|
| workflow → task | 流程照跑，只是不自动生成时限 |
| billing → time | 能开固定费/风险代理的单，开不了计时账单 |
| approval → billing | 单子照走，付完款不回写代垫 |
| approval → crm | 泄漏检查只查案件名，查不了当事人名 |

★ **跨模块传 dict，不传 ORM 对象。** 传裸对象，对面顺着关系属性就能摸到
你整张表，隔离墙白建（见 `time.billable_entries`）。

★ **跨 schema 不建外键，存裸 id。** 外键会把两个模块焊死，
`db purge <模块>` 时对面跟着塌。

## 三道墙（互不替代，别搞混）

律所平台有三种不同性质的"看不见"，实现方式完全不同：

| 墙 | 挡什么 | 怎么实现 | 在哪 |
|---|---|---|---|
| **数据范围** | 你只能看自己的 / 本部门的 / 全所的 | `security.apply_scope()` 加 WHERE | kernel |
| **隔离墙** | 这个案子对你**单独**关闭（利冲放行时设） | `assert_not_walled()`，措辞刻意不说"被隔离" | conflict → case |
| **单据脱敏** | 处理一笔付款时不必知道是哪个案子 | **结构性** —— 单据上根本没有案情字段 | approval |
| **归档锁** | 卷封了就只读，谁都不能再往里写 | `case.assert_writable()`，各模块写入口调 | case → 各模块 |

★ 第三道**不是权限墙**。财务仍有 `case.view`（全所），主动去案件页照样
看得到 —— 2026-08-27 与用户确认维持现状。它挡的是"钱的流程里不出现案情"，
不是"财务接触不到案情"。**把它当成后者会出事。**

## 钱怎么算（billing / consult / approval 共同遵守）

- 一律 `Decimal` / `Numeric(18,2)`。float 落在律师费上就是账单和收款差一分钱
- **`ROUND_HALF_UP`，不用银行家舍入** —— 客户拿计算器按的是四舍五入
- **算出来的数要存下来**（`billed_minutes`、`invoice.total_amount`）。
  口径以后改了，历史单据不能跟着变
- **发出去的不许改**：已审工时、已开账单、已付单据一律锁死，
  要改只能作废/红冲后重来。**一张凭空消失的记录比一张错的更可疑**

## Agent 出口（预留）

```
外部 Agent ──MCP──▶ 能力目录（由各模块 capabilities 汇总）──▶ 各模块 api.py
                          ↑
                  代理身份 + DataScope 过滤 + 隔离墙 + 审计留痕
```

Schema 用 Pydantic 定义（D14），`model_json_schema()` 原生就是 MCP 的 `inputSchema`，零翻译成本。
导出：`python -m kernel.cli capabilities --mcp`
详见手册第 10 章。

## 有意留白（不要提前做）

| 项 | 现状 | 触发条件 |
|---|---|---|
| 事件总线 | `NullEventBus`，调用点用 `# EVENT:` 标记 | ⚠ **条件已满足**（模块 14 个 > 8），见下 |
| MCP 出口 | `capabilities` 已可声明与导出，但无 MCP server | 决定接 Agent 时 |
| 流程模板可视化编辑 | 图用 Python 代码写 | 案由 > 10 个 |
| 多租户物理隔离 | 单库 + `tenant_id` | 有大所要求私有化 |
| 微服务拆分 | 单体，schema 已隔离 | 暂不考虑 |

---

## ⚠ 已经触发但还没做的：事件总线

这份文档在 Step 2 时写下触发条件「模块 > 8 个」。**到 Step 18 已经 14 个，
条件早就满足了，而没人发现 —— 因为这份文档从 Step 2 之后就没人回头读。**

记在这里，是为了下次不再靠"谁想起来"：

**现状**：模块之间全是直接调用（`registry.api(...)`）。这没坏，
边界检查也拦得住乱来。但有两处已经开始别扭：

1. `approval` 付完款要回写 `billing` 的代垫 —— 现在是硬调。
   将来再来个"付完款通知税务模块"，就得在 `pay()` 里再加一行。
2. `workflow` 跑完要生成时限、要落文档、将来还要发提醒 ——
   已经挂了三个 `optional_api`，每加一个下游就要改一次上游。

**判断**：**暂不做**。事件总线会把"谁调了谁"从代码里抹掉，
换来的解耦目前还不值那个可读性代价 —— 一个 14 个模块的单体，
直接调用仍然是能一眼看懂的。

**真正该做的时候**：出现第三个"付完款还要通知 X"的需求时。
届时把 `# EVENT:` 标记的点批量换掉。

**再往后每一步，动手前请回头看一眼这份文档的「有意留白」表。**
它写的条件是给未来的人看的，不回头看就等于没写。
