"use client";

/**
 * 案件流程面板（Step 9 底座 + Step 10 建工流程）。
 *
 * 这个界面要让律师看懂三件事，而且顺序不能变：
 *
 *   ① 有没有要命的时限          → 最上面，红的，带法条依据
 *   ② 该查哪几件事、查到了什么   → 争议焦点 + 依据，标出「它替我搜了什么」
 *   ③ 现在轮到我做什么          → 确认区，能改机器抽错的东西
 *
 * ★ ① 排在最前面是有理由的：优先受偿权是**除斥期间**，过期即失权。
 *   律师接手案子最先该问的就是"还剩几天"，这恰恰最容易被埋在材料里。
 *
 * ★ 流程在后台跑（一趟几十秒），所以这里要轮询。别做成同步等待 ——
 *   律师会以为界面卡死。
 */
import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchWorkflowTemplates, fetchCaseRuns, fetchRun, startRun, resumeRun, cancelRun,
  lookupSources,
  WORKFLOW_STATUS_LABEL, ELEMENT_FIELDS,
  type WorkflowRun, type WorkflowRunDetail, type WorkflowTemplate,
  type Deadline, type IssueResearch, type IssueCitation, type LookupItem,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Notice, Section, Tag, useNotice } from "@/components/ui";

export function WorkflowPanel({ caseId, caseSummary }: {
  caseId: number;
  /** 案件的案情摘要，作为发起流程时的默认输入 */
  caseSummary?: string;
}) {
  const { can } = useAuth();
  const { notice, clear, ok, fail } = useNotice();

  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [detail, setDetail] = useState<WorkflowRunDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [starting, setStarting] = useState(false);
  const [facts, setFacts] = useState(caseSummary || "");
  const [role, setRole] = useState("contractor");
  const [answer, setAnswer] = useState("");
  const [edits, setEdits] = useState<Record<string, string | boolean>>({});
  const [dropped, setDropped] = useState<string[]>([]);
  const inFlight = useRef(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  //: 轮询连续失败几次了。★ 后台轮询必须容忍抖动，见 load() 里的说明
  const misses = useRef(0);

  const canStart = can("workflow.start");
  const canResume = can("workflow.resume");
  const canCancel = can("workflow.cancel");

  const stopPolling = useCallback(() => {
    if (timer.current) { clearInterval(timer.current); timer.current = null; }
  }, []);

  /** 拉一次流程状态。
   *
   * ★ 这是**后台轮询**，不是用户点出来的请求，所以对失败的处理完全不同：
   *
   *   一次失败不能报错，更不能停掉轮询。前端 dev server 重编译、
   *   后端重启、网络抖一下，都会让某一次 fetch 失败。原来的写法是
   *   "失败即 fail() + stopPolling()" —— 结果一次抖动就把轮询永久停掉，
   *   红色横幅永久留在屏幕上，服务恢复了也不会自己好。
   *   （实测就是这么翻的：重启前端那一瞬间撞上一次，界面就卡死在报错上。）
   *
   *   所以：连续失败 3 次才认为真的断了；一旦成功立刻把错误横幅清掉。
   */
  const load = useCallback(async (runId: string) => {
    try {
      const d = await fetchRun(runId);
      if (misses.current > 0) { misses.current = 0; clear(); }
      setDetail(d);

      /* ★ **只有真正结束了才停轮询。**
       *
       *   原来的判断是 `d.status !== "running"` 就停 —— 而
       *   `waiting_human` 也不等于 "running"，于是**一到人工确认点
       *   轮询就停了**。你点「确认并继续」，后端异步把状态推成 running、
       *   跑完变 done，可界面这边早就不看了 ——
       *   屏幕上永远停在「等你确认」。
       *
       *   这就是"确认完了记录还显示等你确认"的原因：不是没保存，
       *   是界面不再刷新。 */
      const finished = ["done", "failed", "cancelled"].includes(d.status);
      if (finished) {
        stopPolling();
      }
      if (d.status !== "waiting_human") {
        // 离开确认点就把输入清掉 —— 否则下一个确认点会带着上一次的字
        setAnswer("");
        setEdits({});
        setDropped([]);
      }
      if (finished || d.status === "waiting_human") {
        setBusy(false);
        fetchCaseRuns(caseId).then(setRuns).catch(() => {});
      }
    } catch (e) {
      misses.current += 1;
      if (misses.current >= 3) {      // 连着三次（约 6 秒）才认输
        stopPolling();
        setBusy(false);
        fail(e);
      }
    }
  }, [caseId, clear, fail, stopPolling]);

  /** 流程在后台跑，轮询到它停下来为止 */
  const poll = useCallback((runId: string) => {
    stopPolling();
    misses.current = 0;
    clear();                          // 重新开跑，把上一次的报错抹掉
    setBusy(true);
    timer.current = setInterval(() => { load(runId); }, 2000);
    load(runId);
  }, [clear, load, stopPolling]);

  useEffect(() => {
    fetchWorkflowTemplates().then(setTemplates).catch(() => {});
  }, []);

  useEffect(() => {
    stopPolling();
    setDetail(null); setStarting(false); setAnswer(""); setEdits({}); setDropped([]);
    setFacts(caseSummary || "");
    fetchCaseRuns(caseId).then((rs) => {
      setRuns(rs);
      const live = rs.find((r) => r.status === "running" || r.status === "waiting_human");
      if (live) poll(live.run_id);
    }).catch(() => {});
    return stopPolling;
  }, [caseId, caseSummary, poll, stopPolling]);

  async function begin(templateKey: string) {
    if (inFlight.current) return;
    if (!facts.trim()) { fail(new Error("案情不能为空 —— 没有案情跑出来的东西是空的，反而误导")); return; }
    inFlight.current = true;
    try {
      const d = await startRun({
        template_key: templateKey, case_id: caseId,
        payload: { facts: facts.trim(), role },
      });
      setStarting(false);
      poll(d.run_id);
    } catch (e) { fail(e); } finally { inFlight.current = false; }
  }

  async function submit() {
    if (!detail || inFlight.current) return;
    if (!answer.trim()) { fail(new Error("确认内容不能为空 —— 这句话会连同你的姓名一起留痕")); return; }
    inFlight.current = true;
    try {
      const data: Record<string, unknown> = { ...edits };
      if (dropped.length) data.drop = dropped;
      await resumeRun(detail.run_id, answer.trim(), data);
      ok("已确认，流程继续");
      poll(detail.run_id);
    } catch (e) { fail(e); } finally { inFlight.current = false; }
  }

  async function stop() {
    if (!detail) return;
    // 中止之后流程不能再继续，只能重新发起 —— 点错一次前面的确认全白做
    if (!window.confirm(
      "确定中止这条流程吗？\n\n" +
      "⚠ 中止后不能再继续，只能重新发起（已花的费用不退）。\n" +
      "checkpoint 会留着供复盘，但流程本身走不下去了。"
    )) return;
    try {
      await cancelRun(detail.run_id, "案件情况变化，中止流程");
      ok("已中止");
      stopPolling();
      load(detail.run_id);
    } catch (e) { fail(e); }
  }

  const tpl = templates.find((t) => t.key === detail?.template_key);
  const st = detail?.state || {};

  return (
    <Section title="办案流程" collapsible rememberKey={`wf-${caseId}`}
             extra={
      canStart && !starting && templates.length > 0 && (
        // ★ 不能因为「本案已经有一条流程」就把发起入口藏起来 ——
        //   之前是这么写的，结果案子里有一条等确认的流程时，
        //   律师根本找不到「发起流程」在哪。一个案子跑几条流程是正常的。
        <button className="btn secondary" onClick={() => { setStarting(true); setDetail(null); }}>
          {detail ? "另起一条" : "发起流程"}
        </button>
      )
    }>
      <Notice text={notice.text} kind={notice.kind} onClose={clear} />

      {/* ---------------- 发起 ---------------- */}
      {starting && (
        <div className="decide-box">
          <div className="muted small" style={{ marginBottom: 6 }}>
            案情写详细些 —— 后面的争议焦点和检索式都是从这段话里的要素推出来的。
          </div>
          <textarea rows={6} value={facts} onChange={(e) => setFacts(e.target.value)}
                    placeholder="合同签订时间、价款、竣工与验收情况、结算与应付款时间、欠付金额、有无招投标/借资质…" />
          <label className="small" style={{ marginTop: 8 }}>我方角色</label>
          <select value={role} onChange={(e) => setRole(e.target.value)} style={{ width: 220 }}>
            <option value="contractor">承包人（施工方）</option>
            <option value="employer">发包人（建设单位）</option>
          </select>
          <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
            {templates.map((t) => (
              <button className="btn" key={t.key} onClick={() => begin(t.key)}>
                {t.label}
                <span className="small" style={{ marginLeft: 6, opacity: .8 }}>
                  {t.steps.length} 步
                </span>
              </button>
            ))}
            <button className="btn secondary" onClick={() => setStarting(false)}>取消</button>
          </div>
        </div>
      )}

      {!detail && !starting && (
        <p className="muted small" style={{ margin: 0 }}>
          {runs.length === 0
            ? "本案还没跑过流程。流程会自动定出争议焦点、算清时限、逐项检索依据 —— 你不用自己想搜什么。"
            : "本案有历史流程记录，见下方。"}
        </p>
      )}

      {/* ---------------- 进度 ---------------- */}
      {detail && (
        <>
          <div className="wf-head">
            <Tag tone={detail.status === "waiting_human" ? "brand" : undefined}>
              {WORKFLOW_STATUS_LABEL[detail.status] ?? detail.status}
            </Tag>
            <span className="muted small">
              {tpl?.label ?? detail.template_key}
              {typeof st.cost_cny === "number" && st.cost_cny > 0 &&
                ` · 已花 ¥${st.cost_cny.toFixed(4)}`}
            </span>
            {canCancel && !["done", "failed", "cancelled"].includes(detail.status) && (
              <button className="btn ghost sm" style={{ marginLeft: "auto" }} onClick={stop}>
                中止
              </button>
            )}
          </div>

          {tpl && (
            <ol className="wf-steps">
              {tpl.steps.map((s) => {
                const done = (st.trail || []).includes(s.key);
                const here = detail.current_node === s.key;
                return (
                  <li key={s.key} className={done ? "done" : here ? "here" : ""}>
                    <span className="dot" />{s.title}
                  </li>
                );
              })}
            </ol>
          )}

          {detail.status === "running" && (
            <p className="muted small">后台推进中，约几十秒 —— 抽要素和起草各要调一次模型。</p>
          )}
          {detail.error && <div className="alert bad">{detail.error}</div>}

          {/* ① 时限最先 —— 除斥期间过了就没了 */}
          {!!(st.deadlines || []).length && <Deadlines items={st.deadlines!} />}

          {/* ② 争议焦点与依据 */}
          {!!(st.research || []).length && <Issues items={st.research!} />}

          {/* 意见书 */}
          {st.draft_error && (
            <div className="alert bad" style={{ marginTop: 12 }}>
              起草失败：{st.draft_error}
            </div>
          )}
          {!!(st.final || st.draft) && (
            <details className="wf-draft" open={detail.status === "waiting_human"}>
              <summary>法律意见书草稿（{(st.final || st.draft || "").length} 字）</summary>
              <div className="answer-box" style={{ marginTop: 8, whiteSpace: "pre-wrap" }}>
                {st.final || st.draft}
              </div>
            </details>
          )}

          {/* ③ 轮到律师 */}
          {detail.status === "waiting_human" && (
            <div className="decide-box">
              <div className="wf-ask">{detail.pending_question}</div>

              {detail.current_node === "confirm_elements" && (
                <ElementEditor elements={st.elements || {}} edits={edits} onChange={setEdits} />
              )}
              {detail.current_node === "confirm_issues" && (
                <IssuePicker items={st.research || []} dropped={dropped} onChange={setDropped} />
              )}

              <label className="small" style={{ marginTop: 10 }}>
                你的确认（会连同你的姓名一起留痕）
              </label>
              <textarea rows={2} value={answer} onChange={(e) => setAnswer(e.target.value)}
                        placeholder="例：要素无误，应付款日期以结算确认单为准" />
              {/* ★ **改到一半要能撤。**
                  原来这里只有「确认并继续」一个按钮 —— 要素改错了、
                  焦点点错了，唯一的出路是「中止流程」，而中止之后
                  前面跑的全白做。**一个只能往前不能后退的表单，
                  人就不敢在上面改东西。** */}
              <div className="row tight" style={{ marginTop: 10 }}>
                <button className="btn" onClick={submit}
                        disabled={busy || !canResume || !answer.trim()}
                        title={!canResume ? "需要 workflow.resume 权限"
                               : !answer.trim() ? "确认内容不能为空 —— 这句话会留痕"
                               : undefined}>
                  确认并继续
                </button>
                <button className="btn secondary" disabled={busy}
                        onClick={() => { setAnswer(""); setEdits({}); setDropped([]); }}>
                  撤销我的修改
                </button>
                <span className="muted small">
                  撤销只清掉这一屏改的东西，<b>流程不会中止</b>
                </span>
              </div>
            </div>
          )}

          {!!detail.steps.length && (
            <details style={{ marginTop: 14 }}>
              <summary className="muted small" style={{ cursor: "pointer" }}>
                流转留痕（{detail.steps.length} 条）—— 谁在什么时候确认了什么
              </summary>
              <table style={{ marginTop: 6 }}>
                <tbody>
                  {detail.steps.map((s) => (
                    <tr key={s.id}>
                      <td style={{ width: 120 }} className="small">{s.node}</td>
                      <td style={{ width: 70 }}><Tag>{s.event}</Tag></td>
                      <td className="muted small">{(s.note || "").slice(0, 70)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </>
      )}

      {runs.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div className="muted small" style={{ marginBottom: 6 }}>本案流程记录</div>
          <table>
            <tbody>
              {runs.slice(0, 6).map((r) => (
                <tr key={r.run_id} style={{ cursor: "pointer" }}
                    onClick={() => { stopPolling(); load(r.run_id); }}>
                  <td style={{ width: 90 }}>
                    <Tag tone={r.status === "waiting_human" ? "brand" : undefined}>
                      {WORKFLOW_STATUS_LABEL[r.status] ?? r.status}
                    </Tag>
                  </td>
                  <td className="small">{r.template_key}</td>
                  <td className="muted small" style={{ textAlign: "right" }}>
                    {r.started_at.slice(5, 16).replace("T", " ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

/** 时限。★ 已过期和除斥期间必须最显眼 */
function Deadlines({ items }: { items: Deadline[] }) {
  return (
    <div style={{ marginTop: 12 }}>
      <div className="muted small" style={{ marginBottom: 6 }}>
        时限体检（本地计算，不花钱）
      </div>
      {items.map((d) => (
        <div key={d.key} className={"deadline dl-" + d.status}>
          <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
            <strong style={{ flex: 1 }}>{d.name}</strong>
            {d.fatal && <span className="badge bad">除斥期间</span>}
            {d.days_left !== null && d.status !== "expired" && (
              <span className="badge">{d.days_left} 天</span>
            )}
          </div>
          <div className="small" style={{ marginTop: 3 }}>{d.message}</div>
          <div className="muted small" style={{ marginTop: 2 }}>
            依据：{d.basis}
            {d.anchor_on && ` · 起算 ${d.anchor_on}`}
          </div>
        </div>
      ))}
    </div>
  );
}

/** 争议焦点与依据。★ 要显示「它替我搜了什么」，否则律师无法复核 */
function Issues({ items }: { items: IssueResearch[] }) {
  return (
    <div style={{ marginTop: 14 }}>
      <div className="muted small" style={{ marginBottom: 6 }}>
        争议焦点 {items.length} 项 —— 由案情要素自动定出，不是关键词猜的
      </div>
      {items.map((r) => (
        <details key={r.key} className="issue">
          <summary>
            <strong>{r.title}</strong>
            <span className="muted small" style={{ marginLeft: 8 }}>
              {r.citations.length} 条依据
            </span>
          </summary>
          <p className="muted small" style={{ margin: "6px 0" }}>{r.why}</p>
          <div className="muted small" style={{ marginBottom: 6 }}>
            它替你搜了：{r.queries.map((q, i) => (
              <code key={i} style={{ marginRight: 6 }}>{q}</code>
            ))}
          </div>
          {r.citations.map((c, i) => (
            <Basis key={i} c={c} />
          ))}
        </details>
      ))}
    </div>
  );
}

/** 要素修正。★ 人说了算 —— 模型抽错的日期在这里改，改完时限会重算 */
function ElementEditor({ elements, edits, onChange }: {
  elements: Record<string, string | boolean>;
  edits: Record<string, string | boolean>;
  onChange: (v: Record<string, string | boolean>) => void;
}) {
  const val = (k: string) => (k in edits ? edits[k] : elements[k] ?? "");

  /** ★ 布尔要素**不能用 JS 真假值读**。
   *
   *  勾掉时写的是字符串 `"否"`，而 `!!"否"` 是 **true** ——
   *  于是勾上之后再也取消不掉，界面永远显示打勾。
   *  （后端 `normalize_elements` 认得 "否"，所以存进去的值一直是对的，
   *   坏的只有这一屏的显示 —— 但律师看到的是"我取消不了"。）
   *
   *  后端只把「是 / 有 / true / 1」当真，其余一律 false —— 这里照它来。 */
  const isOn = (v: unknown) => {
    if (typeof v === "boolean") return v;
    const t = String(v ?? "").trim();
    return t === "是" || t === "有" || t === "true" || t === "1";
  };
  return (
    <div style={{ marginTop: 10 }}>
      <div className="muted small" style={{ marginBottom: 6 }}>
        改这里覆盖机器抽的。<strong>应付款日期最关键</strong> —— 优先受偿权的六个月从它起算。
      </div>
      <div className="elem-grid">
        {ELEMENT_FIELDS.map((f) => (
          <label key={f.key} className="small" style={{ margin: 0 }}>
            {f.label}
            {f.bool ? (
              <div>
                <input type="checkbox" checked={isOn(val(f.key))} style={{ width: "auto" }}
                       onChange={(e) => onChange({ ...edits, [f.key]: e.target.checked ? "是" : "否" })} />
              </div>
            ) : (
              <input value={String(val(f.key) ?? "")}
                     onChange={(e) => onChange({ ...edits, [f.key]: e.target.value })} />
            )}
          </label>
        ))}
      </div>
    </div>
  );
}

function IssuePicker({ items, dropped, onChange }: {
  items: IssueResearch[];
  dropped: string[];
  onChange: (v: string[]) => void;
}) {
  return (
    <div style={{ marginTop: 10 }}>
      <div className="muted small" style={{ marginBottom: 6 }}>
        不需要的焦点勾掉，起草时就不写它。
      </div>
      <div className="wall-picker">
        {items.map((r) => (
          <label key={r.key} className="small">
            <input type="checkbox" checked={dropped.includes(r.key)}
                   style={{ width: "auto", marginRight: 4 }}
                   onChange={(e) => onChange(
                     e.target.checked ? [...dropped, r.key]
                                      : dropped.filter((x) => x !== r.key))} />
            去掉「{r.title}」
          </label>
        ))}
      </div>
    </div>
  );
}


/**
 * 一条依据 —— **点得开**。
 *
 * ★ 原来这里是纯 `<div>`：屏幕上写着「民法典_合同编#第八百零一条」，
 *   律师却点不动它，只看得到一行被截断的片段。
 *
 *   **一条打不开的依据，和没有依据的区别只在于它让人以为查过了。**
 *   而律师复核依据的唯一办法就是读原文 —— 读不到就只能相信机器。
 *
 * ★ 走 `/retrieval/lookup` 按标识反查，只读、0 成本。
 *   查不到就明说查不到（库重建、法条下架都会这样），不装作没事。
 */
function Basis({ c }: { c: IssueCitation }) {
  const [open, setOpen] = useState(false);
  const [full, setFull] = useState<LookupItem | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const stale = c.effective_status.includes("失效")
             || c.effective_status.includes("废止");

  async function toggle() {
    if (open) { setOpen(false); return; }
    setOpen(true);
    if (full || loading) return;
    setLoading(true);
    setErr("");
    try {
      const out = await lookupSources([c.source_id]);
      setFull(out.items[c.source_id] ?? { found: false });
    } catch (e) {
      setErr(e instanceof Error ? e.message : "查不到");
    } finally { setLoading(false); }
  }

  return (
    <div className={"citation" + (stale ? " stale" : "")}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
        <button type="button" className="citelink" style={{ flex: 1 }}
                title="点开看原文" onClick={() => void toggle()}>
          {c.source_id}
        </button>
        <span className={"badge" + (stale ? " danger" : c.effective_status ? " ok" : "")}>
          {c.effective_status || "未标注"}
        </span>
        <span className="muted small">{open ? "收起" : "看原文"}</span>
      </div>

      {!open && (
        <div className="muted small clip" style={{ marginTop: 3 }}>{c.snippet}</div>
      )}

      {open && (
        <div style={{ marginTop: 6 }}>
          {loading && <p className="muted small">正在从法条库取原文…</p>}
          {err && <p className="small bad">{err}</p>}
          {full && !full.found && (
            <p className="muted small">
              法条库里现在查不到这一条 —— 可能是库重建过，或者这条已经下架。
              <br />下面是<b>检索当时存下来的片段</b>：
            </p>
          )}
          {full && !full.found && (
            <div className="cite-snippet">{c.snippet || "（当时没存下片段）"}</div>
          )}
          {full && full.found && (
            <>
              {full.title && <div className="small"><b>{full.title}</b></div>}
              <div className="cite-snippet">
                {full.text || full.snippet || "（库里没有正文）"}
              </div>
              {full.effective_status && full.effective_status !== c.effective_status && (
                <p className="small warn" style={{ marginTop: 4 }}>
                  ⚠ 现在库里标的是「{full.effective_status}」，
                  和检索当时的「{c.effective_status || "未标注"}」不一样 —— 请核对。
                </p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
