"use client";

/**
 * 案件收费（Step 17）。
 *
 * 三块，顺序就是收钱这件事的顺序：
 *
 *   ① 收费约定  —— 签合同时定的，★ **风险代理的合法性在这一步拦**
 *   ② 账单      —— 草稿可改，开出即锁死
 *   ③ 回款      —— 开了单不等于收到钱
 *
 * ★ 顶上摆的是「未收」，不是「已开」。
 *   律所真正的风险不是"没开单"，是"**开了单收不回来**"。
 *   一个已开 80 万、收回 12 万的案子，看「已开」是漂亮的，
 *   看「未收」才知道出了什么事。
 */
import { useCallback, useEffect, useState } from "react";

import {
  fetchCaseBilling, fetchCaseInvoices, fetchEngagement, checkContingency,
  createEngagement, createInvoice, fetchInvoiceLines, addInvoiceLine,
  removeInvoiceLine, pullConsultIntoInvoice, billFixed, billContingency,
  issueInvoice, voidInvoice, fetchPayments, recordPayment, reversePayment,
  FEE_MODE_LABEL, LINE_KIND_LABEL, PAY_METHOD_LABEL, yuan,
  type CaseBilling, type Invoice, type InvoiceLine, type BillingPayment,
  type ContingencyCheck, type User,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Notice, Rich, Section, Tag, useNotice } from "@/components/ui";

const STATUS_TONE: Record<string, "ok" | "warn" | "bad" | "muted"> = {
  draft: "muted", issued: "warn", partial: "warn", paid: "ok", void: "muted",
};

export function BillingPanel({ caseId, users }: {
  caseId: number;
  users: Record<number, User>;
}) {
  const { can } = useAuth();
  const { notice, clear, ok, fail } = useNotice();

  const [summary, setSummary] = useState<CaseBilling | null>(null);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [signing, setSigning] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, inv] = await Promise.all([
        fetchCaseBilling(caseId), fetchCaseInvoices(caseId),
      ]);
      setSummary(s);
      setInvoices(inv);
    } catch (e) { fail(e); }
  }, [caseId, fail]);

  useEffect(() => { void load(); }, [load]);

  const run = useCallback(async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    clear();
    try {
      await fn();
      await load();
      ok(label);
      return true;
    } catch (e) {
      fail(e);
      return false;
    } finally { setBusy(false); }
  }, [clear, load, ok, fail]);

  const canManage = can("billing.manage");
  const canIssue = can("billing.issue");
  const eng = summary?.engagement ?? null;

  return (
    <Section title="收费与账单" collapsible rememberKey={`bill-${caseId}`}
             extra={canManage && (
               <button className="btn secondary" disabled={busy}
                       onClick={() => void run("已建草稿账单",
                         () => createInvoice({ case_id: caseId }))}>
                 + 新建账单
               </button>
             )}>
      <Notice {...notice} onClose={clear} />

      {summary && (
        <div className="row" style={{ gap: 10, flexWrap: "wrap", marginBottom: 10 }}>
          {/* ★ 未收排第一 —— 它才说明问题 */}
          <Stat v={yuan(summary.outstanding)} label="未收"
                tone={Number(summary.outstanding) > 0 ? "warn" : undefined}
                hint="已开出但还没收回来的钱。律所真正的风险在这一格。" />
          <Stat v={yuan(summary.billed)} label="已开" />
          <Stat v={yuan(summary.paid)} label="已收" />
          {summary.draft_count > 0 && (
            <Stat v={String(summary.draft_count)} label="草稿" tone="warn"
                  hint="还没开出去的账单 —— 客户看不到，也收不到钱" />
          )}
        </div>
      )}

      {/* ---------------- 收费约定 ---------------- */}
      <h4>收费约定</h4>
      {eng ? (
        <div className="stack">
          <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
            <Tag tone="ok">{eng.mode_label}</Tag>
            {eng.fixed_amount && <span>包干价 ¥{yuan(eng.fixed_amount)}</span>}
            {eng.contingency_pct && (
              <span>风险比例 <b>{eng.contingency_pct}%</b></span>
            )}
            {eng.base_amount && <span>基础费 ¥{yuan(eng.base_amount)}</span>}
            {eng.consult_rate && (
              <span>咨询 ¥{yuan(eng.consult_rate)}/小时</span>
            )}
            {eng.cap_amount && <span>封顶 ¥{yuan(eng.cap_amount)}</span>}
            {/* ★ 含不含税必须显示出来 —— 这是收费纠纷的前三名 */}
            <Tag tone={eng.tax_included ? "muted" : "warn"}
                 title={eng.tax_included
                          ? "报价已含增值税，客户按这个数付"
                          : "报价不含税，客户实付要再加增值税"}>
              {eng.tax_included ? "含税" : "不含税"} · 增值税 {eng.vat_pct}%
            </Tag>
            {eng.contract_no && <span className="muted small">合同 {eng.contract_no}</span>}
          </div>
          {eng.legality_note && (
            /* ★ 签约时的合法性提示原样留着 —— 收费违规被查时，
               "系统提示过、律师确认过"和"没人提过"是两种处境 */
            <p className="alert warn" style={{ whiteSpace: "pre-wrap" }}>
              <b>签约时的合规提示（已确认）：</b><br />
              <Rich text={eng.legality_note} />
            </p>
          )}
        </div>
      ) : (
        <p className="muted small">还没有收费约定。开账单前先把收费方式定下来。</p>
      )}

      {canManage && (
        <div style={{ marginTop: 8 }}>
          <button className="btn ghost sm" onClick={() => setSigning(!signing)}>
            {signing ? "收起" : eng ? "改约定" : "+ 签收费约定"}
          </button>
          {signing && (
            <EngagementForm caseId={caseId} busy={busy}
                            onSubmit={(b) => void run("约定已保存",
                              () => createEngagement(b))
                              .then((good) => { if (good) setSigning(false); })} />
          )}
        </div>
      )}

      {/* ---------------- 账单 ---------------- */}
      <h4 style={{ marginTop: 18 }}>账单</h4>
      {invoices.length === 0 ? (
        <p className="muted small">还没有账单。</p>
      ) : (
        <table >
          <thead>
            <tr>
              <th>单号</th><th>期间</th><th>合计</th><th>已收</th>
              <th>未收</th><th>状态</th><th />
            </tr>
          </thead>
          <tbody>
            {invoices.map((inv) => (
              <InvoiceRow key={inv.id} inv={inv} users={users} busy={busy}
                          canManage={canManage} canIssue={canIssue}
                          canPay={can("billing.payment")}
                          open={open === inv.id}
                          onToggle={() => setOpen(open === inv.id ? null : inv.id)}
                          onRun={run} eng={eng} />
            ))}
          </tbody>
        </table>
      )}
    </Section>
  );
}

function Stat({ v, label, hint, tone }: {
  v: string; label: string; hint?: string; tone?: "warn" | "bad";
}) {
  return (
    <div className="stack" style={{ minWidth: 110 }} title={hint}>
      <div style={{ fontSize: 21, fontVariantNumeric: "tabular-nums" }} className={tone}>
        {v}
      </div>
      <div className="small">{label}</div>
    </div>
  );
}

// ---------------------------------------------------------------- 一张账单

function InvoiceRow({ inv, users, busy, canManage, canIssue, canPay, open,
                      onToggle, onRun, eng }: {
  inv: Invoice; users: Record<number, User>; busy: boolean;
  canManage: boolean; canIssue: boolean; canPay: boolean; open: boolean;
  onToggle: () => void;
  onRun: (label: string, fn: () => Promise<unknown>) => Promise<boolean>;
  eng: CaseBilling["engagement"];
}) {
  const [lines, setLines] = useState<InvoiceLine[]>([]);
  const [pays, setPays] = useState<BillingPayment[]>([]);
  const [payAmt, setPayAmt] = useState("");
  const [payRef, setPayRef] = useState("");
  const [desc, setDesc] = useState("");
  const [amt, setAmt] = useState("");
  const [kind, setKind] = useState("disbursement");
  const [recovered, setRecovered] = useState("");

  const loadDetail = useCallback(async () => {
    const [l, p] = await Promise.all([
      fetchInvoiceLines(inv.id), fetchPayments(inv.id),
    ]);
    setLines(l);
    setPays(p);
  }, [inv.id]);

  useEffect(() => { if (open) void loadDetail(); }, [open, loadDetail, inv.paid_amount,
                                                    inv.total_amount, inv.status]);

  const run = (label: string, fn: () => Promise<unknown>) =>
    onRun(label, fn).then(async (good) => { if (open) await loadDetail(); return good; });

  return (
    <>
      <tr>
        <td>{inv.number}</td>
        <td className="muted small">
          {inv.period_from || inv.issued_on || "—"}
          {inv.due_on && <div>到期 {inv.due_on}</div>}
        </td>
        <td>¥{yuan(inv.total_amount)}</td>
        <td>¥{yuan(inv.paid_amount)}</td>
        <td className={Number(inv.outstanding) > 0 ? "warn" : undefined}>
          ¥{yuan(inv.outstanding)}
        </td>
        <td><Tag tone={STATUS_TONE[inv.status] ?? "muted"}>{inv.status_label}</Tag></td>
        <td>
          <button className="btn ghost sm" onClick={onToggle}>{open ? "收起" : "明细"}</button>
        </td>
      </tr>

      {open && (
        <tr>
          <td colSpan={7} className="stack">
            {/* 金额构成。★ 分项摆出来，客户加一遍要能对上 */}
            <p className="small" style={{ marginTop: 0 }}>
              律师费 ¥{yuan(inv.fee_amount)}
              {Number(inv.discount_amount) > 0 && <>　折让 −¥{yuan(inv.discount_amount)}</>}
              　增值税({inv.vat_pct}%) ¥{yuan(inv.tax_amount)}
              {Number(inv.disbursement_amount) > 0 && (
                <span title="替客户垫付的钱，原价转付，不计增值税">
                  　代垫 ¥{yuan(inv.disbursement_amount)}
                </span>
              )}
              　<b>合计 ¥{yuan(inv.total_amount)}</b>
            </p>
            {inv.void_reason && (
              <p className="alert warn">已红冲：{inv.void_reason}</p>
            )}

            {lines.length > 0 && (
              <table >
                <tbody>
                  {lines.map((l) => (
                    <tr key={l.id}>
                      <td style={{ width: 70 }}>
                        <Tag tone={l.kind === "discount" ? "warn" : "muted"}>
                          {l.kind_label}
                        </Tag>
                      </td>
                      <td>
                        {l.description}
                        {l.user_id && (
                          <div className="muted small">
                            {users[l.user_id]?.full_name ?? `#${l.user_id}`}
                            {l.hours && `　${l.hours} 小时 × ¥${yuan(l.rate)}`}
                            {/* ★ 客户质疑某一行时，要能翻回原始工时 */}
                            {l.source_entry_ids &&
                              `　工时 #${l.source_entry_ids}`}
                          </div>
                        )}
                      </td>
                      <td style={{ width: 110, textAlign: "right" }}>
                        {l.kind === "discount" ? "−" : ""}¥{yuan(l.amount)}
                      </td>
                      <td style={{ width: 50 }}>
                        {canManage && !inv.locked && (
                          <button className="btn ghost sm" disabled={busy}
                                  onClick={() => void run("已删除",
                                    () => removeInvoiceLine(l.id))}>
                            删
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {/* ---- 草稿可以加东西 ---- */}
            {canManage && !inv.locked && (
              <div style={{ marginTop: 10 }}>
                <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <button className="btn ghost sm" disabled={busy}
                          title="把这个客户还没出账的咨询费拉进来"
                          onClick={() => void run("已拉入咨询",
                            () => pullConsultIntoInvoice(inv.id, {}))}>
                    拉入咨询
                  </button>
                  {eng && ["fixed", "retainer", "hybrid"].includes(eng.mode) && (
                    <button className="btn ghost sm" disabled={busy}
                            onClick={() => void run("已按约定开出固定费",
                              () => billFixed(inv.id, {}))}>
                      按约定开固定费
                    </button>
                  )}
                  {eng && ["contingency", "hybrid"].includes(eng.mode) && (
                    <>
                      <input placeholder="实际收回金额" value={recovered}
                             style={{ width: 140 }} disabled={busy}
                             onChange={(e) => setRecovered(e.target.value)} />
                      <button className="btn ghost sm" disabled={busy || !recovered}
                              title={`按约定的 ${eng.contingency_pct}% 计算`}
                              onClick={() => void run("已按约定开出风险代理费",
                                () => billContingency(inv.id, { recovered }))}>
                        开风险代理费
                      </button>
                    </>
                  )}
                </div>

                <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 8 }}>
                  <select value={kind} disabled={busy}
                          onChange={(e) => setKind(e.target.value)}>
                    {Object.entries(LINE_KIND_LABEL).map(([k, v]) => (
                      <option key={k} value={k}>{v}</option>
                    ))}
                  </select>
                  <input placeholder="这一行是什么（客户逐行看）" value={desc}
                         style={{ minWidth: 260 }} disabled={busy}
                         onChange={(e) => setDesc(e.target.value)} />
                  <input placeholder="金额" value={amt} style={{ width: 110 }}
                         disabled={busy}
                         onChange={(e) => setAmt(e.target.value)} />
                  <button className="btn ghost sm" disabled={busy || !desc || !amt}
                          onClick={() => {
                            void run("已加一行", () => addInvoiceLine(
                              inv.id, { description: desc, amount: amt, kind }))
                              .then((good) => { if (good) { setDesc(""); setAmt(""); } });
                          }}>
                    加一行
                  </button>
                </div>
                {kind === "disbursement" && (
                  <p className="muted small">
                    代垫费用是<b>替客户垫的钱</b>（诉讼费、鉴定费…），原价转付、
                    不加价，也<b>不计增值税</b>。
                  </p>
                )}
              </div>
            )}

            {/* ---- 开出 ---- */}
            {canIssue && inv.status === "draft" && (
              <div style={{ marginTop: 10 }}>
                <button className="btn secondary" disabled={busy || lines.length === 0}
                        onClick={() => {
                          if (window.confirm(
                            `开出账单 ${inv.number}，合计 ¥${yuan(inv.total_amount)}。\n\n` +
                            "★ 开出之后就锁死了，改不了 —— 要更正只能红冲后重开。\n" +
                            "关联的工时也会一并标为已出账。")) {
                            void run("账单已开出", () => issueInvoice(inv.id, {}));
                          }
                        }}>
                  开出账单 ⚠
                </button>
                <p className="muted small">
                  ⚠ 开出即锁死。关联工时会一并标为已出账。
                </p>
              </div>
            )}

            {/* ---- 回款 ---- */}
            {inv.status !== "draft" && inv.status !== "void" && (
              <div style={{ marginTop: 12 }}>
                <b className="small">回款</b>
                {pays.length > 0 && (
                  <table >
                    <tbody>
                      {pays.map((p) => (
                        <tr key={p.id} className={p.reversed ? "muted" : undefined}>
                          <td style={{ width: 100 }}>{p.paid_on}</td>
                          <td style={{ width: 80 }}>{p.method_label}</td>
                          <td>
                            {p.reference || <span className="muted">无流水号</span>}
                            {p.reversed && (
                              <div className="muted small">已冲回：{p.reverse_reason}</div>
                            )}
                          </td>
                          <td style={{ width: 110, textAlign: "right" }}>
                            {p.reversed && "−"}¥{yuan(p.amount)}
                          </td>
                          <td style={{ width: 50 }}>
                            {canPay && !p.reversed && (
                              <button className="btn ghost sm" disabled={busy}
                                      onClick={() => {
                                        const r = window.prompt("冲回原因（会留痕）：");
                                        if (r) void run("已冲回",
                                          () => reversePayment(p.id, r));
                                      }}>
                                冲回
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {canPay && Number(inv.outstanding) > 0 && (
                  <div className="row" style={{ gap: 8, marginTop: 6, flexWrap: "wrap" }}>
                    <input placeholder="金额" value={payAmt} style={{ width: 110 }}
                           disabled={busy}
                           onChange={(e) => setPayAmt(e.target.value)} />
                    <input placeholder="银行流水号（对账时唯一能核的东西）"
                           value={payRef} style={{ minWidth: 240 }} disabled={busy}
                           onChange={(e) => setPayRef(e.target.value)} />
                    <button className="btn ghost sm" disabled={busy || !payAmt}
                            onClick={() => {
                              void run("已登记回款", () => recordPayment(
                                inv.id, { amount: payAmt, reference: payRef }))
                                .then((good) => {
                                  if (good) { setPayAmt(""); setPayRef(""); }
                                });
                            }}>
                      登记回款
                    </button>
                    <span className="muted small">未收 ¥{yuan(inv.outstanding)}</span>
                  </div>
                )}
              </div>
            )}

            {/* ---- 红冲 ---- */}
            {canIssue && inv.status !== "draft" && inv.status !== "void" && (
              <div style={{ marginTop: 10 }}>
                <button className="btn ghost sm" disabled={busy}
                        title="红冲不是删除 —— 记录会留着"
                        onClick={() => {
                          const r = window.prompt(
                            "红冲原因（会留痕，且这张单作废后需要重开）：");
                          if (r) void run("已红冲", () => voidInvoice(inv.id, r));
                        }}>
                  红冲这张单
                </button>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

// ---------------------------------------------------------------- 签约定

/**
 * ★ 这个表单最要紧的不是收集字段，是**在律师填风险比例的当下**
 *   就把合法性告诉他 —— 而不是点了保存才弹一个红框。
 *   律师需要拿这段话去跟客户解释为什么不能这么签。
 */
function EngagementForm({ caseId, busy, onSubmit }: {
  caseId: number; busy: boolean;
  onSubmit: (b: Record<string, unknown>) => void;
}) {
  // ★ 默认「固定收费」不是「计时」—— 计时模式只有封顶价没有金额栏，
  //   一打开就是那个，用户看到的就是"只能填类型，不能填金额"。
  const [mode, setMode] = useState("fixed");
  const [fixedAmount, setFixedAmount] = useState("");
  const [pct, setPct] = useState("");
  const [baseAmount, setBaseAmount] = useState("");
  const [cap, setCap] = useState("");
  const [hourly, setHourly] = useState("");
  const [taxIncluded, setTaxIncluded] = useState(true);
  const [contractNo, setContractNo] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [chk, setChk] = useState<ContingencyCheck | null>(null);

  const isRisk = mode === "contingency" || mode === "hybrid";

  // 选了风险代理就立刻查一次 —— 别等保存
  useEffect(() => {
    if (!isRisk) { setChk(null); return; }
    let alive = true;
    const rate = Number(pct || 0) / 100;
    checkContingency(caseId, rate)
      .then((r) => { if (alive) setChk(r); })
      .catch(() => {});
    return () => { alive = false; };
  }, [isRisk, pct, caseId]);

  const blocked = chk?.level === "block";
  const needConfirm = chk?.level === "confirm";

  return (
    <div className="stack" style={{ marginTop: 8 }}>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <select value={mode} disabled={busy}
                onChange={(e) => setMode(e.target.value)}>
          {Object.entries(FEE_MODE_LABEL).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        {/* ★ **每个模式都要有一个能填钱的地方。**
            原来选中「计时收费」时只有一个"封顶价（可空）"，
            用户的原话是「只能填类型，不能填金额，如何算钱呢」——
            他是对的：一个填不了钱的收费约定，等于没约定。 */}
        {["fixed", "retainer"].includes(mode) && (
          <label className="small">
            {mode === "fixed" ? "包干价" : "年度顾问费"}
            <input placeholder="如 106000" value={fixedAmount} style={{ width: 130 }}
                   disabled={busy} onChange={(e) => setFixedAmount(e.target.value)} />
          </label>
        )}
        {mode === "hybrid" && (
          <label className="small">
            基础费
            <input placeholder="先收的那部分" value={baseAmount} style={{ width: 130 }}
                   disabled={busy} onChange={(e) => setBaseAmount(e.target.value)} />
          </label>
        )}
        {isRisk && (
          <label className="small" title="法定上限 30%">
            风险比例
            <input type="number" step="1" min="0" max="30" value={pct}
                   style={{ width: 70 }} disabled={busy}
                   onChange={(e) => setPct(e.target.value)} /> %
          </label>
        )}
        {mode === "hourly" && (
          <label className="small" title="留空则用本所咨询费率">
            咨询单价
            <input placeholder="元/小时" value={hourly} style={{ width: 110 }}
                   disabled={busy} onChange={(e) => setHourly(e.target.value)} />
          </label>
        )}
        {["hourly", "hybrid"].includes(mode) && (
          <label className="small" title="收到这个数就不再往上收 —— 可以留空">
            封顶
            <input placeholder="可空" value={cap} style={{ width: 110 }}
                   disabled={busy} onChange={(e) => setCap(e.target.value)} />
          </label>
        )}
        <label className="small"
               title="报价含不含增值税 —— 这是律所收费纠纷的前三名，别留白">
          <input type="checkbox" checked={taxIncluded} disabled={busy}
                 onChange={(e) => setTaxIncluded(e.target.checked)} /> 报价含税
        </label>
        <input placeholder="合同编号" value={contractNo} style={{ width: 140 }}
               disabled={busy} onChange={(e) => setContractNo(e.target.value)} />
      </div>

      {/* ★ 合法性提示：当场显示，不等保存 */}
      {chk && chk.level !== "allow" && (
        <div className={blocked ? "stakebox small" : "warnbox small"}
             style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>
          <b>{blocked ? "这个案子不能做风险代理" : "这个案由部分禁止风险代理"}</b>
          <br />
          <Rich text={chk.message} />
          {chk.caveat && (
            <>
              <br /><b>盖不住：</b><Rich text={chk.caveat} />
            </>
          )}
        </div>
      )}

      {needConfirm && !blocked && (
        <label className="small" style={{ display: "block", marginTop: 8 }}>
          <input type="checkbox" checked={confirmed} disabled={busy}
                 onChange={(e) => setConfirmed(e.target.checked)} />
          {" "}我已核对本案诉讼请求，<b>不含</b>上述禁止风险代理的事项
          <span className="muted">（这一确认会记录在案）</span>
        </label>
      )}

      <div style={{ marginTop: 8 }}>
        <button className="btn ghost sm"
                disabled={busy || blocked || (needConfirm && !confirmed)}
                onClick={() => onSubmit({
                  case_id: caseId, mode,
                  fixed_amount: fixedAmount || null,
                  contingency_rate: pct ? String(Number(pct) / 100) : null,
                  base_amount: baseAmount || null,
                  cap_amount: cap || null,
                  consult_rate: hourly || null,
                  tax_included: taxIncluded,
                  contract_no: contractNo,
                  confirm_legality: confirmed,
                })}>
          保存约定
        </button>
        {blocked && (
          <span className="muted small">　法定禁止，改成别的收费方式吧。</span>
        )}
      </div>
    </div>
  );
}
