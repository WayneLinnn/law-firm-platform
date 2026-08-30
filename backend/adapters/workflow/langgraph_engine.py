"""WorkflowPort 的 LangGraph 实现。

**全项目只有这个包 import langgraph。** 上层拿到的永远是 `WorkflowRun`
这个朴素 dataclass，换编排引擎业务代码零改动。

本文件只做一件事：**把业务声明的 `Template`（Step 列表）编译成一张图。**
建工的时限怎么算、争议焦点怎么拆，全在 `modules/workflow` 里 ——
适配器不认识"建设工程"这四个字，这是铁律「adapters 不得 import modules」逼出来的，
但结果比原来好：业务知识没有落进一个只该管"怎么跑图"的地方。

三件容易踩的事，写在前面：

1. **checkpoint 表放在 workflow schema 里，靠 search_path 顶进去。**
   `PostgresSaver.setup()` 建的是不带 schema 前缀的 checkpoints /
   checkpoint_writes / checkpoint_blobs / checkpoint_migrations，
   落在 search_path 的第一个 schema。不钉死就会掉进 public，
   于是「每个模块独占一个 schema」这条铁律破功，`db purge workflow`
   也清不干净。这里在连接串上钉 `options=-csearch_path%3Dworkflow`。

2. **这些表不写 Alembic 迁移。** 它们是 LangGraph 的私有存储，
   结构随库版本变，我们手写迁移必然和库打架。由 `setup()` 自己建，
   我们只保证 schema 存在。删模块时 `DROP SCHEMA workflow CASCADE`
   会把它们一并带走 —— 可拆卸性没有破。
   （`kernel/db/migrations/env.py` 里的 ENGINE_OWNED_TABLES 负责
   让 autogenerate 装作没看见它们，否则会生成 drop_table 把在跑的流程抹掉。）

3. **人工确认只在这一层**（手册 C1）。检索那张图一律 `human=False`：
   挂起的图配上同步 HTTP 会把请求吊死。要挂起，只能挂在这里。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from typing_extensions import Annotated, TypedDict

from kernel.config import Settings
from kernel.errors import DomainError, NotFound
from kernel.ports.workflow import Template, WorkflowRun

logger = logging.getLogger(__name__)

SCHEMA = "workflow"

#: 一步走完后写进 state 的当前位置，给界面显示"走到哪了"
LAST_NODE = "__last_node__"
#: 节点抛错时写进 state 的错误信息
ERROR = "__error__"

#: 业务状态在图里的通道名。
#:
#: ★ 为什么要套一层，而不是直接用 `StateGraph(dict)`：
#:   用普通 dict 当 state schema 时，LangGraph 把整个 dict 当**一个**通道，
#:   默认 reducer 是"后者覆盖前者" —— 于是每个节点的返回值会把上一个节点的
#:   state 整个替换掉。实测：起点传进去的 facts 跑完第一个节点就没了，
#:   律师确认写回的 confirmed 也留不下来，只有恰好被最后一个节点返回的键活着。
#:   这种丢法很隐蔽：trail 因为每个节点都读了再写，看起来一直是对的。
#:
#:   所以把业务状态整个塞进一个通道，并给它一条**合并** reducer。
#:   业务侧完全无感：Step 的回调拿到的还是那个朴素 dict。
DATA = "data"


def _merge(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """通道的合并规则：后来的键覆盖同名键，其余原样留着。"""
    out = dict(old or {})
    out.update(new or {})
    return out


class GraphState(TypedDict, total=False):
    """图的状态容器。★ 必须定义在**模块顶层**。

    本文件开头有 `from __future__ import annotations`，注解是延迟求值的，
    LangGraph 解析 TypedDict 时按**模块 globals** 去找 `Annotated` ——
    定义在函数里就会报 `NameError: name 'Annotated' is not defined`。
    """

    data: Annotated[Dict[str, Any], _merge]


def _psycopg_dsn(database_url: str) -> str:
    """SQLAlchemy 的 URL 转成 psycopg 能直接用的 DSN，并钉死 search_path。

    `postgresql+psycopg://…` 里那个 `+psycopg` 是 SQLAlchemy 的方言标记，
    psycopg 不认，必须去掉。
    """
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    dsn = dsn.replace("postgresql+psycopg2://", "postgresql://", 1)
    # ★ 见文件头第 1 条：不钉 search_path，checkpoint 表会掉进 public
    sep = "&" if "?" in dsn else "?"
    return "{0}{1}options={2}".format(dsn, sep, quote("-csearch_path=" + SCHEMA))


def _answer_of(reply: Any) -> str:
    """律师的答复。兼容两种形态：一个字符串，或 {answer, data}。"""
    if isinstance(reply, dict):
        return str(reply.get("answer", ""))
    return "" if reply is None else str(reply)


def _data_of(reply: Any) -> Dict[str, Any]:
    """答复里附带的结构化修正（比如律师把抽错的竣工日期改了）。"""
    if isinstance(reply, dict):
        got = reply.get("data")
        return dict(got) if isinstance(got, dict) else {}
    return {}


class LangGraphEngine:
    """实现 kernel.ports.WorkflowPort。

    一个模板 = 一张编译好的图。图是无状态的，状态全在 checkpointer 里，
    所以图可以进程内缓存复用，而**流程本身跨进程、跨天、跨人**。
    """

    def __init__(self, settings: Settings, templates: Optional[Dict[str, Template]] = None):
        self._dsn = _psycopg_dsn(settings.database_url)
        self._templates: Dict[str, Template] = dict(templates or {})
        self._saver = None
        self._compiled: Dict[str, Any] = {}
        self._ready = False

    # ------------------------------------------------------------ 注册

    def register(self, template: Template) -> None:
        """登记业务模块声明的流程模板。重复登记以最后一次为准（便于热改）。"""
        self._templates[template.key] = template
        self._compiled.pop(template.key, None)

    def template(self, key: str) -> Template:
        tpl = self._templates.get(key)
        if tpl is None:
            raise NotFound(
                "没有这个流程模板：{0}；可用：{1}".format(
                    key, "、".join(sorted(self._templates)) or "无"
                ),
                code="workflow.no_template",
            )
        return tpl

    # ------------------------------------------------------------ 内部

    def _checkpointer(self):
        """惰性建 checkpointer。**只在真正跑流程时才连库、才建表。**"""
        if self._saver is not None:
            return self._saver

        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        pool = ConnectionPool(
            conninfo=self._dsn,
            max_size=4,
            # psycopg 默认 autocommit=False；PostgresSaver 要自己管事务
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=True,
        )
        saver = PostgresSaver(pool)
        # schema 由 Alembic 迁移建（模块自己的表也在里面），这里只保证它在
        with pool.connection() as conn:
            conn.execute("CREATE SCHEMA IF NOT EXISTS {0}".format(SCHEMA))
        saver.setup()          # 建 checkpoints 等私有表，幂等
        self._saver = saver
        logger.info("workflow checkpointer 就绪（schema=%s）", SCHEMA)
        return saver

    def _build(self, tpl: Template):
        """把 Step 列表编译成一张线性图。

        ★ **一个 Step 可能编译成两个节点。**

        LangGraph 的 `interrupt()` 不是"就地挂起"：恢复时**整个节点函数
        从头重跑一遍**，`interrupt()` 那一行才返回律师的答复。所以如果把
        run 和 ask 塞进同一个节点，run 会被执行两次 —— 抽要素多花一次钱、
        检索多跑一轮，state 里还可能出现重复内容。

        靠"跑过就跳过"的标记去挡是挡不住的：标记本身也是节点的返回值，
        而节点没跑完就 interrupt 了，标记根本没写进 state。

        所以直接拆开：run 一个节点，ask+apply 一个节点。
        两个节点之间有 checkpoint，重跑只会重跑 ask 那半边 ——
        而 ask 只是"把问题再问一遍"，本来就是幂等的。
        """
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import interrupt

        def run_node(step):
            def node(graph_state: GraphState) -> Dict[str, Any]:
                state = dict(graph_state.get(DATA) or {})
                try:
                    out = dict(step.run(state) or {})
                except Exception as exc:          # 节点炸了要留痕，不能静默
                    logger.exception("流程步骤 %s 执行失败", step.key)
                    return {DATA: {ERROR: "{0}: {1}".format(type(exc).__name__, exc),
                                   LAST_NODE: step.key}}
                out[LAST_NODE] = step.key
                if step.ask is None:              # 没有人工确认，这一步到此为止
                    out["trail"] = list(state.get("trail") or []) + [step.key]
                return {DATA: out}

            return node

        def ask_node(step):
            def node(graph_state: GraphState) -> Dict[str, Any]:
                state = dict(graph_state.get(DATA) or {})
                question = step.ask(state) or ""
                if not question:                  # 这次不用问人，直接过
                    return {DATA: {"trail": list(state.get("trail") or []) + [step.key],
                                   LAST_NODE: step.key}}
                reply = interrupt(question)       # ← 恢复时从本函数开头重来
                out: Dict[str, Any] = {}
                if step.apply is not None:
                    try:
                        out.update(step.apply(
                            state, _answer_of(reply), _data_of(reply)) or {})
                    except Exception as exc:
                        logger.exception("流程步骤 %s 回填失败", step.key)
                        return {DATA: {ERROR: "{0}: {1}".format(type(exc).__name__, exc),
                                       LAST_NODE: step.key}}
                out["trail"] = list(state.get("trail") or []) + [step.key]
                out[LAST_NODE] = step.key
                return {DATA: out}

            return node

        g = StateGraph(GraphState)
        prev = START
        for step in tpl.steps:
            if step.run is not None:
                name = step.key if step.ask is None else step.key + "::run"
                g.add_node(name, run_node(step))
                g.add_edge(prev, name)
                prev = name
            if step.ask is not None:
                g.add_node(step.key, ask_node(step))
                g.add_edge(prev, step.key)
                prev = step.key
        g.add_edge(prev, END)
        return g

    def _graph(self, template_key: str):
        if template_key in self._compiled:
            return self._compiled[template_key]
        graph = self._build(self.template(template_key)).compile(
            checkpointer=self._checkpointer())
        self._compiled[template_key] = graph
        return graph

    def _snapshot(self, graph, run_id: str, template_key: str, case_id: str) -> WorkflowRun:
        """把 LangGraph 的内部状态翻译成上层认识的 WorkflowRun。

        ★ 这个翻译层是刻意的：`StateSnapshot`、`Interrupt` 这些类型属于
          langgraph，一旦泄露到上层，换引擎就得改业务代码。
        """
        snap = graph.get_state({"configurable": {"thread_id": run_id}})
        state = dict((snap.values or {}).get(DATA) or {})

        # 停在人工确认点：LangGraph 用 interrupt 表达
        pending = None
        for task in snap.tasks or ():
            for intr in getattr(task, "interrupts", ()) or ():
                value = getattr(intr, "value", None)
                pending = value if isinstance(value, str) else str(value)
                break
            if pending:
                break

        if state.get(ERROR):
            status = "failed"
        elif pending is not None:
            status = "waiting_human"
        elif snap.next:
            status = "running"
        elif not state and not snap.tasks:
            # ★ 空快照 ≠ 跑完了。
            #   后台线程刚被 spawn、还没写下第一个 checkpoint 时，
            #   这个 thread_id 在库里根本不存在，get_state 返回一张空快照。
            #   把它当 done 会让轮询的客户端以为流程秒完，
            #   还会被 _sync 反手写进业务表 —— 实测就这么翻过车。
            status = "running"
        else:
            status = "done"

        return WorkflowRun(
            run_id=run_id,
            template_key=template_key,
            case_id=str(case_id),
            status=status,
            current_node=(snap.next[0] if snap.next else state.get(LAST_NODE, "")),
            state=state,
            pending_question=pending,
        )

    # ------------------------------------------------------------ WorkflowPort

    def templates(self) -> List[str]:
        return sorted(self._templates)

    def describe(self) -> List[Dict[str, Any]]:
        """给界面用：模板 key、人话名字、几步、每步叫什么。"""
        out = []
        for key in sorted(self._templates):
            tpl = self._templates[key]
            out.append({
                "key": key,
                "label": tpl.label,
                "steps": [{"key": s.key, "title": s.title} for s in tpl.steps],
            })
        return out

    def start(
        self,
        template_key: str,
        case_id: str,
        payload: Dict[str, Any],
        run_id: Optional[str] = None,
    ) -> WorkflowRun:
        """跑到第一个人工确认点（或跑完）为止。

        run_id 由调用方给 —— 业务侧要先落一条记录再让它跑，
        否则后台线程跑完了都不知道该往哪张表写。
        """
        tpl = self.template(template_key)
        missing = [f for f in tpl.requires if not str(payload.get(f, "")).strip()]
        if missing:
            raise DomainError(
                "发起 {0} 缺少必填项：{1}".format(tpl.label, "、".join(missing)),
                code="workflow.missing_payload",
            )
        graph = self._graph(template_key)
        run_id = run_id or uuid.uuid4().hex
        config = {"configurable": {"thread_id": run_id}}
        state = dict(payload or {})
        state.setdefault("case_id", str(case_id))
        state.setdefault("trail", [])
        try:
            graph.invoke({DATA: state}, config=config)
        except Exception as exc:  # 节点抛错不能把整个请求打成 500
            logger.exception("流程 %s 启动失败", run_id)
            raise DomainError(
                "流程启动失败：{0}".format(exc), code="workflow.start_failed"
            ) from exc
        return self._snapshot(graph, run_id, template_key, case_id)

    def resume(self, run_id: str, human_input: Dict[str, Any]) -> WorkflowRun:
        """律师回答人工确认点后继续。"""
        template_key = str(human_input.get("__template__") or "")
        case_id = str(human_input.get("__case_id__") or "")
        graph = self._graph(template_key)
        config = {"configurable": {"thread_id": run_id}}

        from langgraph.types import Command

        payload = {
            "answer": human_input.get("answer", ""),
            "data": human_input.get("data") or {},
        }
        try:
            graph.invoke(Command(resume=payload), config=config)
        except Exception as exc:
            logger.exception("流程 %s 恢复失败", run_id)
            raise DomainError(
                "流程恢复失败：{0}".format(exc), code="workflow.resume_failed"
            ) from exc
        return self._snapshot(graph, run_id, template_key, case_id)

    def get(self, run_id: str) -> WorkflowRun:
        raise NotFound(
            "引擎不保存模板归属，请用 modules.workflow.service.get_run 查",
            code="workflow.use_service",
        )

    def state_of(self, template_key: str, run_id: str, case_id: str) -> WorkflowRun:
        """按模板取状态。service 层知道 template_key，所以由它来调。"""
        return self._snapshot(self._graph(template_key), run_id, template_key, case_id)


def build_workflow_adapter(settings: Settings) -> Any:
    """给 bootstrap 用的工厂。backend=fake 时用内存实现，不连库也能开发。"""
    from adapters.workflow.templates import BUILTIN_TEMPLATES

    if getattr(settings, "workflow_backend", "langgraph") == "fake":
        from adapters.workflow.fake import FakeWorkflowEngine

        return FakeWorkflowEngine(BUILTIN_TEMPLATES)
    return LangGraphEngine(settings, BUILTIN_TEMPLATES)
