# 模块开发规范

> 对应到 Step 18。三条铁律是 Step 2 定的，**下面「十二条经验」是 Step 3–21 与试用回归
> 一步步撞出来的** —— 每条后面都写了它是被什么事故逼出来的。
> 写新模块前把这两部分都过一遍，能省掉重犯一遍的功夫。

## 三条铁律

1. **业务模块只能 import `kernel.*`**，不得 import 任何 `adapters.*`
   —— 需要外部能力时用 `container.get("model" | "storage" | "retrieval" | "legal_qa" | ...)`
2. **模块之间只能访问对方的 `api.py`**，禁止跨 schema 直接查表
   —— 用 `registry.api("crm")`（必需依赖）或 `registry.optional_api("crm")`（可选依赖，可降级）
3. **每个模块自带 `migrations/`**，删模块 = 代码与建表脚本一起消失

违反前两条会被 `scripts/check_boundaries.py` 拦下，CI 直接失败。

---

## 新建一个模块

```bash
mkdir -p backend/modules/<name>/migrations/versions
```

必须有的文件：

| 文件 | 作用 |
|---|---|
| `manifest.py` | `MANIFEST = ModuleManifest(name=..., requires=..., optional=..., permissions=..., capabilities=...)` |
| `api.py` | 对外唯一出口，只 re-export 公开函数 |
| `service.py` | 业务逻辑 |
| `models.py` | ORM，`__table_args__ = schema_of("<name>")` |
| `router.py` | 定义 `router = APIRouter()`，内核自动挂到 `/api/<name>` |

生成迁移：

```bash
python -m kernel.cli db revision <name> -m "init"
python -m kernel.cli db upgrade
```

---

## 五步流程（不跳）

```
1. 定契约   写 api.py 的签名 + Pydantic 数据类，不写实现
2. 写测试   针对接口写用例，此时全红
3. 建模型   models.py + 迁移
4. 填实现   service.py，直到测试全绿
5. 接路由   router.py
```

---

## manifest 怎么写

```python
from pydantic import BaseModel, Field
from kernel.manifest import Capability, ModuleManifest


class ListDueParams(BaseModel):
    case_id: str = Field(default="", description="限定案件，留空表示全部")
    within_days: int = Field(default=7, ge=1, le=90)


MANIFEST = ModuleManifest(
    name="task",
    version="0.1.0",
    description="任务与法定时限",
    requires=("case",),        # 缺了它，系统拒绝启动
    optional=("notify",),      # 缺了它，本模块降级运行

    # 权限点：业务代码只判断这些，永不判断角色名
    permissions=("task.view", "task.manage"),

    # Agent 能力：现在只声明不消费，将来一步导出成 MCP tools（见手册第 10 章）
    capabilities=(
        Capability(
            name="task.listDue",
            description="列出某案件或某人的临期任务",
            params=ListDueParams,
            requires_perm="task.view",
            read_only=True,     # 写操作必须显式 False，且要二次确认 + 审计
        ),
    ),
)
```

**每写一个模块顺手声明 capabilities。** 攒齐了将来接 Agent 就是一步遍历；
现在不写，将来要回头翻十几个模块补声明。

验证：`python -m kernel.cli capabilities --mcp`

---

## 依赖怎么写

```python
from kernel.registry import registry

# 必需依赖：对方不在就该报错
crm = registry.api("crm")
client = crm.get_client(session, client_id)

# 可选依赖：对方不在就降级
conflict = registry.optional_api("conflict")
if conflict is not None:
    conflict.check(session, case)      # 有就查
# 没有就直接放行，系统照常工作
```

---

## 表怎么定义

```python
# models.py —— 每个模块独占一个 schema，删模块 = DROP SCHEMA
from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from kernel.db.base import Base, schema_of


class Task(Base):
    __tablename__ = "task"
    __table_args__ = schema_of("task")     # ← 不要用默认 public

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
```

---

## 外部能力怎么用

```python
from kernel.container import container

model = container.get("model")            # ModelPort，背后是 DeepSeek
result = model.chat([...], json_schema=MySchema.model_json_schema())

retrieval = container.get("retrieval")    # 纯检索：0 成本、可缓存、可随便重跑
qa = container.get("legal_qa")            # 问答：有成本、有 trace、任务化
```

**永远不会出现 `from adapters.model.deepseek import ...`。** 换实现只改 `bootstrap.py`。

---

## 事件（预留）

现在用直接调用，但在调用点留标记：

```python
# EVENT: case.created
```

★ 「模块超过 8 个」这个条件**在 Step 18 已经满足**（14 个），
但评估后暂不做，理由见 [ARCHITECTURE.md](ARCHITECTURE.md) 文末。
真做的时候，把这些点批量替换成
`container.get("events").publish(Event("case.created", {...}))`。

---

## 十二条经验（Step 3–21 ＋ 试用回归撞出来的）

三条铁律管的是"模块之间别乱来"。这十二条管的是"模块自己别写错"——
每一条都对应一次真实的返工。

### 1. 规则是数据，不是代码

律所会改的东西一律进表：时限规则（`task.deadline_rule`）、
计时口径（`time.time_policy`）、费率（`billing.fee_rate`）、
审批流转（`approval.flow_rule`）。

**判断标准**：这东西各所不同、或者会改吗？会 → 进表。

★ **例外要写清楚**：法定的东西**硬编在校验里**，不给律所改的余地。
比如风险代理 30% 上限、除斥期间不能中断 —— 那是规章和法律性质，
做成配置项等于允许违规。代码里要写明"这不是本所政策"。

### 2. 算不出来要明说，绝不猜

缺数据时给一个"看起来合理"的默认值，是本项目最贵的一类错。

| 模块 | 场景 | 做法 |
|---|---|---|
| task | 缺起算日 | 状态 `unknown`，并**点名缺哪个日期** |
| billing | 某人没配费率 | **整个动作失败并点名是谁**，不按 0 算 |
| doc | 扫描件抽不出正文 | `text_status=empty`，单独列出来 |

★ 理由都一样：**按 0 / 按默认值算出来的结果看着很正常，
所以没人会发现。** 而刺眼的"算不出"至少会让人去查。

★ 同一类里还要**分档**：「缺数据算不出」和「本来就没有」是两回事
（`task` 的 `unknown` vs `no_fixed`）。混成一个，
永远变不成绿色的那条会把真警报一起拖下水 —— **假警报会吃掉真警报。**

### 3. 报错必须给出口

只说"不行"的报错，等于把问题丢回给用户。每条 `DomainError` 都要能回答
"那我该怎么办"。

```python
# 不好
raise DomainError("工时超过上限", code="time.entry_too_long")

# 好
raise DomainError(
    "单条工时 {0} 小时超过上限（{1} 小时）—— 这多半是录错了，"
    "真有这么长请拆成几条，说明各自做了什么。".format(...),
    code="time.entry_too_long")
```

★ 还要注意**几道校验别互相堵死**：Step 16 有过一次「超 8 小时要确认」+
「单条不超 12 小时」两条独立规则，用户确认完还是记不进去，
卡在没有出口的死路上。

### 4. 发出去的数字要钉死

已审的工时、已开的账单、已付的单据一律锁死。要改只能作废/红冲后重来。

- **作废不是删除** —— 一张凭空消失的记录比一张错的更可疑
- 作废/驳回/红冲**必须写理由** —— 不说为什么，对方只会原样再提一次，
  来回三趟最后变成闭眼批
- 算出来的数**存下来**（`billed_minutes`、`invoice.total_amount`）：
  口径以后改了，历史单据不能跟着变

### 5. 幂等接口返回「结果」不是「增量」

`ensure_default_*` 第二次调用要返回**本所现有的全套**，不是"这次新建的"
（那会是空列表，界面据此渲染成"一条都没有"）。

★ 而且要**补空**：给内置规则新加字段后，已经灌过的租户补不上 ——
**新库对、老库错，且不报任何错**，这是最难发现的一类。
`ensure_default_rules` 现在会补"还停在默认值"的字段，
不碰律所改过的值（见 `time`/`task` 的实现）。

### 6. 跨模块只传 dict，不传 ORM 对象

传裸对象，对面顺着关系属性就能摸到你整张表 —— 隔离墙白建。
参见 `time.billable_entries()`：它返回 dict，
并且**"什么样的工时能进账单"这条规则归 time**，
不让 billing 自己拼一遍（两边各写一份，迟早对不上）。

跨 schema **不建外键**，存裸 id。外键会把两个模块焊死。

### 7. 每个模块都要有 `describe()`

ORM → 给界面用的 dict，统一在这里做三件事：

1. 加人话标签（`status` → `status_label`）
2. **按查看者脱敏**（`approval.describe()` 会抹掉看不见的案件，
   并**明说抹掉了** —— 悄悄抹成 null，人会去猜、去问、去在备注里补，
   那才是隔离墙真正会漏的地方）
3. 算派生值（`outstanding`、`days_left`、`locked`）

### 8. 分权：做事的人和批事的人分开

写权限点时先问一句"这两件事该不该同一个人做"：

- 不能审自己的工时 / 批自己的单（硬拦在 service 里，不靠角色配置）
- 财务**只付款不批单** —— 既批又付是财务内控第一条红线
- 业务口能谈收费、编草稿，**开单和收款是财务的** —— 否则没有第二双眼睛
- 盖章的人不该是批准盖章的人

★ 权限点命名 `<模块>.<动作>`，业务代码**只判断权限点，永不判断角色名**。

---

### 9. 拆模块：删目录**不够**，还要清它的迁移头

2026-08-28 拆掉 `time`（工时）时踩到的。

模块是自动发现的，所以删掉 `modules/time/` 目录，注册表里它就消失了 ——
**但数据库里还留着它的 alembic 分支头**：

```
alembic.util.exc.CommandError: Can't locate revision identified by 'b6a9c6d018e5'
```

`kernel.alembic_version` 里存着每个分支的当前 revision。
脚本删了、记录还在，下一次 `db upgrade` 就去找一个不存在的 revision。
**而且它是在迁移阶段炸的，不是启动阶段** —— 老库升级时才暴露。

拆一个模块的完整动作：

```bash
rm -rf modules/<name> tests/test_<name>.py     # 1. 代码

# 2. 清引用：optional_api / manifest 的 optional=(...) / 角色权限点
grep -rn '"<name>' modules/ | grep -v "^modules/<name>/"

# 3. 清迁移头和 schema
delete from kernel.alembic_version where version_num='<它的分支头>';
drop schema if exists "<name>" cascade;

python -m kernel.cli check      # 4. 边界
pytest -q                        # 5. 全量
```

★ 拆得动的前提是**当初就没让别人硬依赖它**。`time` 在整个后端只被
`billing` 一处 `optional_api` 引用，所以其他 14 个模块一行没改。
如果当初图省事在 billing 里 `from modules.time.models import TimeEntry`，
这次就是一场大手术 —— 边界检查拦的就是这个。

### 10. 别把别人行业的做法照搬过来

同样来自拆 `time` 那次，而且这条比上面那条贵得多。

工时模块是照着**英美计时制律所**做的：人人每天记满 8 小时、
乘以计费率、逐级审批、月底出账。1752 行，做得挺完整。

**但中国的提成制律所根本不这么运转** —— 团队成员和律师助理拿的是
工资或分成，他们的时间不换算成客户账单。

硬套下去的结果不是"多了个没人用的功能"，是更糟的东西：
**所有人应付式地补录，数据全是假的。而假数据比没数据危险得多 ——
它会被拿去算成本、算人效、算分成。**

★ 更该记住的是：这个错在 Step 21 已经露过一次头。当时用户说
「工时不用计，记录创收就可以」，我照做了（看板里一个工时指标都没有），
**却没有回头问一句"那这个模块还留着干什么"。**

一个功能失去了它唯一的出口，就该当场提出来 ——
留着它比删掉更容易让人以为它有用。

**下手前先问一句：这个流程在中国的律所里，是谁、在什么时候、为什么做？**
答不上来就别照着国外的产品做。

### 11. 屏幕上出现的东西，就该能点

2026-08-28 用户第一次从头点到尾，八个问题里**四个是同一个病**：

| 位置 | 表现 |
|---|---|
| 看板 14 个指标 | 点哪个都跳到同一张全量表 |
| 办案流程的依据 | 写着法条编号，点不动 |
| 文书节标题的「引用 S1、S2」 | 点不动 |
| 引用体检表的【S2】 | 点不动 |

★ 更难堪的是，「**点不开的数字是装饰**」这句话是我自己在 Step 21
写进文档的，然后在四个地方各违反了一次。
**写下原则和执行原则是两件事。**

具体规矩：

- **一个数字点开之后，看到的必须正好是那个数字本身。**
  从"12 条算不出"点进去看到全量表，等于没链接 ——
  律师还得自己再找一遍那 12 条。
- **口径要复用同一段代码。** 格子里的数和明细列表各算一遍，迟早会漂；
  一漂用户不会觉得是"筛选条件不同"，只会觉得**统计算错了**，
  然后整个看板都不可信了。
- **范围在汇总层拼，不在模块里。** 同一个 `case.open`，
  工作台上是"我经办的"、全所页上是"全所的" —— 模块不知道也不该知道。
- **筛过的列表必须自己说出来**，并给一个"看全部"的出口。
  一张悄悄筛过的表比不筛更糟：行数比预期少，
  用户第一反应是"系统漏数据了"。
- **同一份数据只弹一张卡。** 出处卡渲染在页签之外，
  切页签时它还挂着，另一个组件又弹一张 —— 屏幕上两张一模一样的。

### 12. 加了字段要有人填，加了函数要有人调

同一轮里的两个错，长得不像但根子一样：

**`createClient` 写好了、导出了、从头到尾没被调用过。** 界面上只有
「新建主体」，用户建完主体发现客户表一行没多 —— 他只会认为保存失败了。

**「计时收费」是收费约定的默认模式，而它只有"封顶价（可空）"没有金额栏。**
用户的原话：「只能填类型，不能填金额，如何算钱呢」。他是对的 ——
**一个填不了钱的收费约定，等于没约定。**

两条检查：

- 写完一个 service 函数，**grep 一下谁调它**。没人调就是没做完，
  不是"接口先留着"。
- 每个表单**每一个分支**都自己走一遍。默认值那个分支尤其要走 ——
  它是最多人看到的，也最容易被跳过（写的人总在测别的分支）。

★ 第二条还有个更狠的版本：**默认值决定了大多数人看到什么。**
把 `hourly` 放在收费模式第一位，等于让所有人第一眼看到的是那个
填不了钱的表单。改法只是把 `fixed` 挪到第一位 —— 一行，但影响的是全部人。

## 测试怎么写

每个测试的 docstring 要写清**它守着什么真实事故**，而不是重复函数名：

```python
def test_repeated_interruptions_cannot_punch_through_twenty_years(...):
    """★ 这条测试是整个时限引擎存在的理由。

    一个每年发一次催款函的案子，每次中断都把届满日推后三年。
    不设上限的话，系统会一路显示「正常」到第 21 年 ——
    一个笃定的绿色，底下什么都没有。
    """
```

理由很实际：半年后有人要改这段逻辑，他看到的是这句话。
写"测试中断功能"，他会以为随便改改没事。

**修 bug 时先补一条钉住它的测试**，docstring 里写明第一版错在哪 ——
本项目好几条最有价值的测试都是这么来的（见 `test_billing.py` 里
"一张同时有工时和代垫的账单开不出去"那条）。

---

## 提交前自查

```bash
pytest
python -m kernel.cli check          # 边界检查
python -m kernel.cli modules        # 确认新模块被发现、依赖解析正确
python -m kernel.cli capabilities   # 确认能力目录正确

# 拆卸测试
LFP_DISABLED_MODULES=<name> python -m kernel.cli modules
```

**还要做的两件事**（Step 18 之前经常漏）：

1. **给角色补权限**。`access.py` 的 `DEFAULT_ROLES` 只对**新建**租户生效，
   已有的 demo 库要跑补权脚本，否则界面上什么都点不动。
2. **更新文档**：
   - `docs/DEVELOPMENT_PLAN.md` —— 这一步做了什么、为什么这么做、
     修掉了什么、**已知边界**
   - `docs/ARCHITECTURE.md` —— 只在架构层面变了才动（新模块要进模块表）
   - 本文件 —— 撞出新经验时补一条
