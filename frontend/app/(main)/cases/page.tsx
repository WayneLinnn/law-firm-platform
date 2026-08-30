"use client";

/**
 * 案件。
 *
 * ★ **结构照 Clio 的 Matter 页重做。**
 *
 *   原来这一页是**一根 9 节的滚动条**：利冲、流程、文书、时限、咨询、
 *   账单、案卷、检索、归档全部平铺。要看账单得滚过前面八个 ——
 *   用户说的"太散了"，主要就是这里。
 *
 *   现在是**列表 → 详情页签**：
 *
 *       概览 | 办案 | 文书 | 时限 | 案卷 | 财务 | 检索 | 卷宗
 *
 *   ★ 「概览」是从 Clio 学来的关键一步：它不是又一个功能面板，
 *     而是**这个案子此刻的全貌** —— 核心字段、当事人、承办人、
 *     能往哪推。律师点进一个案子，八成只是想看这一屏。
 *
 * ★ 页签**按权限整个隐藏，不是禁用**（Clio 也这么做：没有开票权限的人
 *   根本看不到 Bills 那个页签）。禁用的按钮只会让人反复去点。
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchCases, fetchCaseDetail, fetchCauses, fetchParties, fetchUsers,
  fetchTabCounts,
  transitionCase, ApiError,
  CASE_STATUS_LABEL, PARTY_ROLE_LABEL, MATTER_KIND_LABEL, MEMBER_ROLE_LABEL,
  IRREVERSIBLE_STATUSES,
  type CaseRow, type CaseDetail, type Cause, type Party, type User,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import {
  Badge, Button, Card, CardContent, CardHeader, DataTable, Notice, Section,
  Tabs, useNotice, useTabs, type Column, type TabDef,
} from "@/components/ui";
import { DrillBanner, useDrill } from "@/components/Drill";
import { SearchPanel } from "@/components/SearchPanel";
import { ConflictPanel } from "@/components/ConflictPanel";
import { WorkflowPanel } from "@/components/WorkflowPanel";
import { DocPanel } from "@/components/DocPanel";
import { DraftPanel } from "@/components/DraftPanel";
import { TaskPanel } from "@/components/TaskPanel";
import { ConsultPanel } from "@/components/ConsultPanel";
import { BillingPanel } from "@/components/BillingPanel";
import { ArchivePanel } from "@/components/ArchivePanel";
import { NewCase } from "@/components/NewCase";

const DRILL_LABEL: Record<string, string> = {
  open: "在办案件", new: "本期新增", closed: "本期结案",
};

export default function CasesPage() {
  const { can, me } = useAuth();
  const { notice, clear, ok, fail } = useNotice();
  const drill = useDrill();
  const [showOthers, setShowOthers] = useState(false);

  const [cases, setCases] = useState<CaseRow[]>([]);
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [causes, setCauses] = useState<Cause[]>([]);
  const [parties, setParties] = useState<Party[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [keyword, setKeyword] = useState("");
  const [status, setStatus] = useState("");
  const [creating, setCreating] = useState(false);

  const canManage = can("case.manage");
  const canClose = can("case.close");

  // ★ 从看板点进来时把口径原样传给后端 —— 不能在前端自己筛，
  //   否则条数和看板上的数字对不上，用户会以为统计算错了
  const reload = useCallback(() => {
    fetchCases({
      keyword, status,
      f: drill.f,
      mine: drill.scope === "mine",
      org_id: drill.scope === "team" && drill.org ? drill.org : undefined,
      since: drill.since || undefined,
      until: drill.until || undefined,
    }).then(setCases).catch(fail);
  }, [keyword, status, drill.f, drill.scope, drill.org,
      drill.since, drill.until, fail]);

  useEffect(() => {
    fetchCauses().then(setCauses).catch(() => {});
    fetchParties("").then(setParties).catch(() => {});
    fetchUsers().then(setUsers).catch(() => {});
  }, []);
  useEffect(() => { reload(); }, [reload]);

  /** 支持 /cases?id=N 直接打开 —— 看板和待办上每一行都指向一个案件 */
  useEffect(() => {
    const id = Number(new URLSearchParams(window.location.search).get("id"));
    if (id > 0) fetchCaseDetail(id).then(setDetail).catch(fail);
  }, [fail]);

  const causeById = useMemo(
    () => Object.fromEntries(causes.map((c) => [c.id, c])), [causes]);
  const partyById = useMemo(
    () => Object.fromEntries(parties.map((p) => [p.id, p])), [parties]);
  const userById = useMemo(
    () => Object.fromEntries(users.map((u) => [u.id, u])), [users]);

  async function open(row: CaseRow) {
    try { setDetail(await fetchCaseDetail(row.id)); } catch (e) { fail(e); }
  }

  /** 利冲处置后刷新。★ 这里可能 403 —— 放行时设的隔离墙可能把**你自己**
   *  挡在本案外面。那不是错误，是墙生效了，要说人话。 */
  const refreshDetail = useCallback(async (caseId: number) => {
    try {
      setDetail(await fetchCaseDetail(caseId));
    } catch (e) {
      if (e instanceof ApiError && e.isForbidden) {
        setDetail(null);
        fail(new Error("隔离墙已生效，你已看不到本案 —— " +
                       "需要有「设置隔离墙」权限的人到本案里撤销"));
      } else { fail(e); }
    }
    reload();
  }, [fail, reload]);

  /* ★ **把"我主办的"和"别人的"分开。**
   *
   *   李明能看到全所 11 个案子（他是高级合伙人，数据范围＝全所），
   *   但其中只有 4 个是他主办的。一张混在一起的表，
   *   第一眼给的是"全所有多少案子"—— 而他真正要找的是**我的**。
   *
   *   有权限看得到 ≠ 需要一直看着。别人的案子折起来，要查时再展开。
   */
  const myCases = cases.filter((c) => c.lead_id === me?.user.id);
  const others = cases.filter((c) => c.lead_id !== me?.user.id);

  const columns: Column<CaseRow>[] = [
    { key: "code", title: "编号", width: 110 },
    { key: "title", title: "案件" },
    { key: "cause", title: "案由", width: 150,
      render: (c) => (c.cause_id ? causeById[c.cause_id]?.name ?? "—" : "—") },
    { key: "status", title: "状态", width: 84,
      render: (c) => (
        <Badge tone={c.status === "active" ? "brand" : "outline"}>
          {CASE_STATUS_LABEL[c.status] ?? c.status}
        </Badge>
      ) },
    { key: "lead", title: "主办", width: 84,
      render: (c) => userById[c.lead_id]?.full_name ?? `用户${c.lead_id}` },
  ];

  return (
    <>
      <Notice text={notice.text} kind={notice.kind} onClose={clear} />

      <DrillBanner drill={drill} n={cases.length}
                   label={DRILL_LABEL[drill.f] ?? "筛选"} />

      {creating && (
        <NewCase
          onCancel={() => setCreating(false)}
          onCreated={async (id) => {
            setCreating(false);
            reload();
            try { setDetail(await fetchCaseDetail(id)); } catch (e) { fail(e); }
          }}
        />
      )}

      <Card>
        <CardHeader
          title={`我主办的案件 · ${myCases.length}`}
          desc="别人的案子折在下面 —— 看得到不等于要一直看着"
          action={
            <div className="row tight">
              {canManage && !creating && (
                <Button variant="primary" onClick={() => setCreating(true)}>
                  新建案件
                </Button>
              )}
              <input value={keyword} onChange={(e) => setKeyword(e.target.value)}
                     placeholder="搜编号 / 名称 / 案号" style={{ width: 190 }} />
              <select value={status} onChange={(e) => setStatus(e.target.value)}
                      style={{ width: 120 }}>
                <option value="">全部状态</option>
                {Object.entries(CASE_STATUS_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </div>
          }
        />
        <CardContent flush>
          <DataTable columns={columns} rows={myCases} onRowClick={open}
                     activeId={detail?.id ?? null}
                     empty="你名下没有案件" />
        </CardContent>
      </Card>

      {/* ★ 别人的案子**默认折起来**。看得到不等于要一直看着 —— 
          李明有全所范围，但他每天真正在办的只有那 4 个。 */}
      {others.length > 0 && (
        <Card>
          <CardHeader
            title={`其他同事的案件 · ${others.length}`}
            desc="你的数据范围能看到，但不是你主办的"
            action={
              <Button onClick={() => setShowOthers(!showOthers)}>
                {showOthers ? "收起" : "展开"}
              </Button>
            }
          />
          {showOthers && (
            <CardContent flush>
              <DataTable columns={columns} rows={others} onRowClick={open}
                         activeId={detail?.id ?? null} empty="没有" />
            </CardContent>
          )}
        </Card>
      )}

      {detail && (
        <CaseDetailView
          key={detail.id}
          detail={detail}
          causeById={causeById}
          partyById={partyById}
          userById={userById}
          canManage={canManage}
          canClose={canClose}
          onRefresh={refreshDetail}
          onNotice={(k, t) => (k === "ok" ? ok(t) : fail(new Error(t)))}
        />
      )}
    </>
  );
}

// ================================================================ 详情

function CaseDetailView({ detail, causeById, partyById, userById,
                          canManage, canClose, onRefresh, onNotice }: {
  detail: CaseDetail;
  causeById: Record<number, Cause>;
  partyById: Record<number, Party>;
  userById: Record<number, User>;
  canManage: boolean;
  canClose: boolean;
  onRefresh: (id: number) => void;
  onNotice: (k: "ok" | "error", t: string) => void;
}) {
  const { can } = useAuth();

  /* ★ 页签上的计数：**不点开就知道哪里有东西**（Clio 也这么做）。
   *
   *   数据直接复用归档模块的 `inventory()` —— 它本来就在问
   *   「这个案子下面各有几件」。**不另写一套计数**：
   *   两套迟早会漂，然后页签上写 3、点进去 5，
   *   用户就不再相信任何一个数字了。 */
  const [counts, setCounts] = useState<Record<string, number>>({});
  useEffect(() => {
    if (!can("archive.view")) return;
    fetchTabCounts(detail.id).then(setCounts).catch(() => {});
  }, [detail.id, can]);

  /* ★ 页签按权限**整个隐藏**，不是禁用。
       禁用的按钮只会让人反复去点，然后来问"为什么点不动"。 */
  const tabs: TabDef[] = [
    { key: "overview", label: "概览" },
    { key: "work",     label: "办案",  count: counts.work,
      hidden: !can("workflow.view") },
    { key: "draft",    label: "文书",  count: counts.draft,
      hidden: !can("draft.view") },
    { key: "deadline", label: "时限",  count: counts.deadline,
      hidden: !can("task.view") },
    { key: "doc",      label: "案卷",  count: counts.doc,
      hidden: !can("doc.view") },
    { key: "money",    label: "财务",  count: counts.money,
      hidden: !can("billing.view") },
    { key: "search",   label: "检索",  hidden: !can("retrieval.search") },
    { key: "archive",  label: "卷宗",  count: counts.archive,
      hidden: !can("archive.view") },
  ];

  // ★ 记住上次停在哪个页签 —— 律师一天里会反复进出同一个案子
  const [tab, setTab] = useTabs(tabs, "lfp-case-tab");

  return (
    <Card>
      {/* --- 抬头：不管在哪个页签都看得见「这是哪个案子」--- */}
      <CardHeader
        title={
          <span className="row tight">
            <span>{detail.title}</span>
            <Badge tone={detail.status === "active" ? "brand" : "outline"}>
              {CASE_STATUS_LABEL[detail.status] ?? detail.status}
            </Badge>
          </span>
        }
        desc={
          <>
            {detail.code}
            {detail.external_no ? ` · ${detail.external_no}` : ""}
            {detail.court ? ` · ${detail.court}` : ""}
            {" · 主办 "}
            {userById[detail.lead_id]?.full_name ?? `用户${detail.lead_id}`}
          </>
        }
      />

      <CardContent>
        <Tabs tabs={tabs} value={tab} onChange={setTab} />

        {tab === "overview" && (
          <Overview detail={detail} causeById={causeById} partyById={partyById}
                    userById={userById} canManage={canManage} canClose={canClose}
                    onRefresh={onRefresh} onNotice={onNotice} />
        )}

        {tab === "work" && (
          <>
            {/* 利冲在流程之前 —— 先决定能不能接，再谈怎么办。顺序就是办案顺序 */}
            <ConflictPanel caseId={detail.id} parties={partyById} users={userById}
                           onDecided={() => onRefresh(detail.id)} />
            <WorkflowPanel caseId={detail.id} caseSummary={detail.summary || ""} />
          </>
        )}

        {tab === "draft" && <DraftPanel caseId={detail.id} />}

        {tab === "deadline" && <TaskPanel caseId={detail.id} users={userById} />}

        {tab === "doc" && <DocPanel caseId={detail.id} users={userById} />}

        {tab === "money" && (
          <>
            {/* ★ 咨询挂客户，案件只是顺带关联 —— 没挂客户的案子不显示 */}
            {detail.client_id && (
              <ConsultPanel clientId={detail.client_id} caseId={detail.id}
                            onNotice={onNotice} />
            )}
            <BillingPanel caseId={detail.id} users={userById} />
          </>
        )}

        {tab === "search" && <SearchPanel caseId={detail.id} />}

        {tab === "archive" && (
          <ArchivePanel caseId={detail.id} users={userById} />
        )}
      </CardContent>
    </Card>
  );
}

// ================================================================ 概览

/**
 * ★ 概览不是"又一个功能面板"，是**这个案子此刻的全貌**。
 *   律师点进一个案子，八成只是想看这一屏：办到哪了、谁在办、
 *   对手是谁、下一步能往哪走。
 */
function Overview({ detail, causeById, partyById, userById,
                    canManage, canClose, onRefresh, onNotice }: {
  detail: CaseDetail;
  causeById: Record<number, Cause>;
  partyById: Record<number, Party>;
  userById: Record<number, User>;
  canManage: boolean;
  canClose: boolean;
  onRefresh: (id: number) => void;
  onNotice: (k: "ok" | "error", t: string) => void;
}) {
  const [busy, setBusy] = useState(false);

  async function go(to: string) {
    const label = CASE_STATUS_LABEL[to] ?? to;
    // ★ 不可逆的状态没有回头路 —— 点错一次就完了，必须先问一句
    if (IRREVERSIBLE_STATUSES.includes(to)) {
      const yes = window.confirm(
        `确定把「${detail.code} ${detail.title}」推进到「${label}」吗？\n\n` +
        `⚠ 这是不可逆动作 —— 「${label}」是终态，之后不能再退回` +
        `「${CASE_STATUS_LABEL[detail.status] ?? detail.status}」。\n` +
        `如果只是想暂停，请不要用这个。`
      );
      if (!yes) return;
    }
    setBusy(true);
    try {
      await transitionCase(detail.id, to);
      onNotice("ok", `已推进到「${label}」`);
      onRefresh(detail.id);
    } catch (e) {
      onNotice("error", e instanceof Error ? e.message : "推进失败");
    } finally { setBusy(false); }
  }

  const canGo = detail.next_statuses.length > 0;

  return (
    <>
      {/* ★ 推进按钮提到最上面做成一条**动作条**。
          原来它在右栏 —— 窄屏塌成一列时排到第 4 位，
          而这是这一屏**唯一的动作**，不该埋在三张表下面。 */}
      <div className="actionbar">
        <span className="muted small">下一步</span>
        {!canGo ? (
          <span className="small">终态，不能再推进</span>
        ) : (
          detail.next_statuses.map((s) => {
            const irreversible = IRREVERSIBLE_STATUSES.includes(s);
            const blocked = busy || !canManage || (irreversible && !canClose);
            return (
              <Button key={s} size="sm" disabled={blocked}
                      variant={irreversible ? "ghost" : "secondary"}
                      title={blocked
                        ? (irreversible ? "需要 case.close 权限"
                                        : "需要 case.manage 权限")
                        : undefined}
                      onClick={() => void go(s)}>
                {CASE_STATUS_LABEL[s] ?? s}{irreversible ? " ⚠" : ""}
              </Button>
            );
          })
        )}
        {canGo && (
          <span className="muted small grow" style={{ textAlign: "right" }}>
            ⚠ 不可逆，需 case.close 权限
          </span>
        )}
      </div>

    <div className="grid aside">
      <div className="stack">
        <Section title="案件信息" flush>
          <table className="kv">
            <tbody>
              <tr><th>案由</th>
                  <td>{detail.cause_id
                        ? causeById[detail.cause_id]?.name ?? "—" : "—"}</td></tr>
              <tr><th>法院</th><td>{detail.court || "—"}</td></tr>
              <tr><th>案号</th><td>{detail.external_no || "—"}</td></tr>
              <tr><th>标的额</th>
                  <td className="num">
                    {detail.amount
                      ? `¥${Number(detail.amount).toLocaleString()}`
                      : <span className="muted">—</span>}
                  </td></tr>
              <tr><th>立案日</th><td>{detail.opened_on ?? "—"}</td></tr>
              <tr><th>案情</th>
                  <td>{detail.summary || <span className="muted">—</span>}</td></tr>
            </tbody>
          </table>
        </Section>

        <Section title="当事人"
                 desc="指向全所主体库 —— 利冲检查靠它跨案件比对"
                 flush>
          {detail.parties.length === 0 ? (
            <div className="empty">尚未登记当事人</div>
          ) : (
            <table>
              <tbody>
                {detail.parties.map((p) => (
                  <tr key={p.id}>
                    <td>{partyById[p.party_id]?.name ?? `主体${p.party_id}`}</td>
                    <td className="small" style={{ width: 104 }}>
                      {PARTY_ROLE_LABEL[p.role] ?? p.role}
                    </td>
                    <td style={{ width: 64 }}>
                      {p.is_ours
                        ? <Badge tone="brand">我方</Badge>
                        : <Badge tone="outline">对方</Badge>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>

        <Section title="事项" desc="程序阶段可以并行，所以独立成表" flush>
          {detail.matters.length === 0 ? (
            <div className="empty">暂无事项</div>
          ) : (
            <table>
              <tbody>
                {detail.matters.map((m) => (
                  <tr key={m.id}>
                    <td>{m.name}</td>
                    <td style={{ width: 90 }}>
                      <Badge tone="outline">
                        {MATTER_KIND_LABEL[m.kind] ?? m.kind}
                      </Badge>
                    </td>
                    <td className="muted small" style={{ width: 100 }}>
                      {m.opened_on ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      </div>

      <div className="stack">
        <Section title="承办人" flush>
          <table>
            <tbody>
              {detail.members.map((m) => (
                <tr key={m.id}>
                  <td>{userById[m.user_id]?.full_name ?? `用户${m.user_id}`}</td>
                  <td style={{ width: 74 }}>
                    <Badge tone="outline">
                      {MEMBER_ROLE_LABEL[m.role] ?? m.role}
                    </Badge>
                  </td>
                </tr>
              ))}
              {detail.members.length === 0 && (
                <tr><td className="muted">暂无承办人</td></tr>
              )}
            </tbody>
          </table>
        </Section>

        <Section title="状态流转" flush>
          {detail.history.length === 0 ? (
            <div className="empty">暂无流转</div>
          ) : (
            <table>
              <tbody>
                {detail.history.map((h) => (
                  <tr key={h.id}>
                    <td className="small">
                      {CASE_STATUS_LABEL[h.from_status] ?? h.from_status}
                      {" → "}
                      {CASE_STATUS_LABEL[h.to_status] ?? h.to_status}
                    </td>
                    <td className="muted small" style={{ width: 76 }}>
                      {h.actor_id
                        ? userById[h.actor_id]?.full_name ?? h.actor_id
                        : "系统"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      </div>
    </div>
    </>
  );
}
