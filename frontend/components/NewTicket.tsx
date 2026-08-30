"use client";

/**
 * 提单表单（Step 18）。
 *
 * ★ 这个表单最要紧的一件事，是在律师写「事由」的**当下**就告诉他
 *   这一栏谁会看到 —— 而不是等他写完「宏远诉凯达工程款的鉴定费」、
 *   点了保存、后端拦下来再说。
 *
 *   后端那道检查（案件名、当事人名出现在事由里就拒绝）是兜底，
 *   它必然漏 —— 案由、争议焦点这些系统认不出来。真正靠得住的是
 *   让人在写的时候就知道这句话要给谁看。
 *
 * ★ 事由**不从案件自动带出来**。那是最省事、也最会泄漏的做法：
 *   省下两秒钟，代价是每张单子都带着案由标题在所里流转。
 */
import { useCallback, useEffect, useState } from "react";

import {
  createTicket, submitTicket, fetchCases,
  TICKET_KIND_LABEL, SEAL_KIND_LABEL,
  type CaseRow,
} from "@/lib/api";

/** 好的事由长什么样 —— 照着填，比读一段说明有用 */
const EXAMPLES: Record<string, string[]> = {
  payment: ["诉讼费垫付", "保全担保费垫付", "造价鉴定费垫付", "公告费垫付"],
  reimburse: ["出差交通费", "开庭差旅住宿", "档案复印费"],
  seal: ["授权委托书用印", "施工合同用印", "对账函用印"],
  disburse: ["退还当事人多缴款项", "代付执行款"],
  other: ["其他事项"],
};

export function NewTicket({ busy, onDone, onNotice, caseId }: {
  busy: boolean;
  onDone: () => void;
  onNotice: { ok: (s: string) => void; fail: (e: unknown) => void; clear: () => void };
  /** 从案件页进来时预置案件 */
  caseId?: number;
}) {
  const [kind, setKind] = useState("payment");
  const [subject, setSubject] = useState("");
  const [amount, setAmount] = useState("");
  const [payee, setPayee] = useState("");
  const [account, setAccount] = useState("");
  const [detail, setDetail] = useState("");
  const [pickedCase, setPickedCase] = useState<string>(caseId ? String(caseId) : "");
  const [cases, setCases] = useState<CaseRow[]>([]);
  const [sealKind, setSealKind] = useState("firm");
  const [sealDoc, setSealDoc] = useState("");
  const [sealCopies, setSealCopies] = useState("1");
  const [takeaway, setTakeaway] = useState(false);
  const [working, setWorking] = useState(false);

  useEffect(() => {
    if (caseId) return;
    fetchCases({}).then(setCases).catch(() => {});
  }, [caseId]);

  const isSeal = kind === "seal";
  const needsMoney = ["payment", "reimburse", "disburse"].includes(kind);

  const submit = useCallback(async (andSubmit: boolean) => {
    setWorking(true);
    onNotice.clear();
    try {
      const t = await createTicket({
        kind, subject, detail,
        amount: needsMoney ? amount : "0",
        case_id: pickedCase ? Number(pickedCase) : null,
        payee, payee_account: account,
        seal_kind: isSeal ? sealKind : "",
        seal_document: isSeal ? sealDoc : "",
        seal_copies: isSeal ? Number(sealCopies || 0) : 0,
        seal_takeaway: isSeal ? takeaway : false,
      });
      if (andSubmit) await submitTicket(t.id);
      onNotice.ok(andSubmit ? "已提交审批" : "已存为草稿");
      onDone();
    } catch (e) {
      // ★ 失败时**不关表单** —— 事由被拦下来时，人正要改的就是那句话
      onNotice.fail(e);
    } finally { setWorking(false); }
  }, [kind, subject, detail, amount, needsMoney, pickedCase, payee, account,
      isSeal, sealKind, sealDoc, sealCopies, takeaway, onNotice, onDone]);

  const disabled = busy || working;

  return (
    <div className="stack" style={{ marginBottom: 12 }}>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <select value={kind} disabled={disabled}
                onChange={(e) => { setKind(e.target.value); setSubject(""); }}>
          {Object.entries(TICKET_KIND_LABEL).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        {needsMoney && (
          <input placeholder="金额" value={amount} style={{ width: 120 }}
                 disabled={disabled} onChange={(e) => setAmount(e.target.value)} />
        )}
        {!caseId && (
          <select value={pickedCase} disabled={disabled}
                  onChange={(e) => setPickedCase(e.target.value)}>
            <option value="">不挂案件</option>
            {cases.map((c) => (
              <option key={c.id} value={c.id}>{c.code} {c.title}</option>
            ))}
          </select>
        )}
      </div>

      {/* ★ 事由那一栏，连同它的警告 */}
      <div style={{ marginTop: 8 }}>
        <input style={{ width: "100%" }} value={subject} disabled={disabled}
               placeholder="事由（一句话）"
               onChange={(e) => setSubject(e.target.value)} />
        <p className="alert warn" style={{ marginTop: 4 }}>
          <b>这一栏财务、行政、用印管理都看得到。</b>
          不要写案件名称、当事人、案由 —— 他们处理这笔钱不需要知道是哪个案子，
          而案件关联系统已经存了。
          <br />
          照着填：
          {(EXAMPLES[kind] || []).map((x) => (
            <button key={x} type="button" className="btn ghost sm"
                    style={{ marginLeft: 6 }} disabled={disabled}
                    onClick={() => setSubject(x)}>
              {x}
            </button>
          ))}
        </p>
      </div>

      {needsMoney && (
        <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 6 }}>
          <input placeholder="收款方" value={payee} style={{ minWidth: 220 }}
                 disabled={disabled} onChange={(e) => setPayee(e.target.value)} />
          <input placeholder="收款账号" value={account} style={{ minWidth: 220 }}
                 disabled={disabled} onChange={(e) => setAccount(e.target.value)} />
        </div>
      )}

      {isSeal && (
        <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 6 }}>
          <select value={sealKind} disabled={disabled}
                  onChange={(e) => setSealKind(e.target.value)}>
            {Object.entries(SEAL_KIND_LABEL).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
          <input placeholder="盖在什么文件上" value={sealDoc} style={{ minWidth: 240 }}
                 disabled={disabled} onChange={(e) => setSealDoc(e.target.value)} />
          <label className="small">
            份数
            <input type="number" min="1" value={sealCopies} style={{ width: 70 }}
                   disabled={disabled}
                   onChange={(e) => setSealCopies(e.target.value)} />
          </label>
          <label className="small" title="原件带出所，风险更高">
            <input type="checkbox" checked={takeaway} disabled={disabled}
                   onChange={(e) => setTakeaway(e.target.checked)} /> 原件外带
          </label>
        </div>
      )}

      <input style={{ width: "100%", marginTop: 6 }} value={detail}
             disabled={disabled}
             placeholder="补充说明（可空）—— 同样会跟着单子走，别写案情"
             onChange={(e) => setDetail(e.target.value)} />

      <div className="row" style={{ gap: 8, marginTop: 8 }}>
        <button className="btn secondary" disabled={disabled || !subject.trim()}
                onClick={() => void submit(true)}>
          提交审批
        </button>
        <button className="btn ghost sm" disabled={disabled || !subject.trim()}
                onClick={() => void submit(false)}>
          存草稿
        </button>
      </div>
    </div>
  );
}
