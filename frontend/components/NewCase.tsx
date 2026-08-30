"use client";

/**
 * 建案（补 Step 6 漏掉的界面 —— 后端一直有接口，前端没做）。
 *
 * ★ 建案时**当事人和案情一定要填**，不是可选项：
 *
 *   当事人  → 利冲检查靠它跨案件比对；文书的原告/被告直接从它填
 *   案情    → 流程从它抽要素，写多细流程就跑多准
 *
 *   只填编号名称建出来的案子，利冲查不出东西、文书填不出当事人、
 *   流程抽不出要素 —— 三个模块一起空转。所以这两项在界面上标出来。
 */
import { useEffect, useState } from "react";

import {
  createCase, addCaseParty, fetchCauses, fetchUsers, fetchParties,
  fetchOrgUnits, createParty, fetchSimilarParties,
  PARTY_ROLE_LABEL,
  type Cause, type User, type Party,
} from "@/lib/api";
import { Notice, Section, useNotice } from "@/components/ui";
import { CausePicker } from "@/components/CausePicker";

type Row = { party_id: number; role: string; is_ours: boolean };

export function NewCase({ onCreated, onCancel }: {
  onCreated: (caseId: number) => void;
  onCancel: () => void;
}) {
  const { notice, clear, ok, fail } = useNotice();

  const [causes, setCauses] = useState<Cause[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [parties, setParties] = useState<Party[]>([]);
  const [orgs, setOrgs] = useState<{ id: number; name: string; kind: string }[]>([]);

  const [code, setCode] = useState("");
  const [title, setTitle] = useState("");
  const [causeId, setCauseId] = useState<number | "">("");
  const [leadId, setLeadId] = useState<number | "">("");
  const [orgId, setOrgId] = useState<number | "">("");
  const [court, setCourt] = useState("");
  const [externalNo, setExternalNo] = useState("");
  const [amount, setAmount] = useState("");
  const [openedOn, setOpenedOn] = useState("");
  const [summary, setSummary] = useState("");

  const [rows, setRows] = useState<Row[]>([]);
  const [newName, setNewName] = useState("");
  const [similar, setSimilar] = useState<Party[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetchCauses().then(setCauses).catch(() => {});
    fetchUsers().then(setUsers).catch(() => {});
    fetchParties("").then(setParties).catch(() => {});
    fetchOrgUnits().then((o) => setOrgs(o.filter((x) => x.kind === "team"))).catch(() => {});
  }, []);

  // 建主体前先查重 —— 主体库是全所共用的，重复主体会让利冲失效
  useEffect(() => {
    const n = newName.trim();
    if (n.length < 2) { setSimilar([]); return; }
    const t = setTimeout(() => {
      fetchSimilarParties(n).then(setSimilar).catch(() => setSimilar([]));
    }, 400);
    return () => clearTimeout(t);
  }, [newName]);

  async function addNewParty(isOurs: boolean) {
    const n = newName.trim();
    if (!n) return;
    try {
      const p = await createParty({ name: n, kind: "org", allow_similar: true });
      setParties((xs) => [...xs, p]);
      setRows((r) => [...r, { party_id: p.id, role: isOurs ? "plaintiff" : "defendant", is_ours: isOurs }]);
      setNewName(""); setSimilar([]);
    } catch (e) { fail(e); }
  }

  async function submit() {
    // ★ 编号留空由后端自动生成 AJ2026-0001 —— 只有名称是必填的
    if (!title.trim()) { fail(new Error("案件名称必填")); return; }
    setBusy(true);
    try {
      const c = await createCase({
        code: code.trim(), title: title.trim(),   // code 空串 = 让后端生成
        cause_id: causeId === "" ? null : Number(causeId),
        lead_id: leadId === "" ? null : Number(leadId),
        org_unit_id: orgId === "" ? null : Number(orgId),
        court: court.trim(), external_no: externalNo.trim(),
        amount: amount.trim() || null,
        opened_on: openedOn || null,
        summary: summary.trim(),
      });
      for (const r of rows) {
        await addCaseParty(c.id, r);
      }
      ok(`已建案 ${c.code}${rows.length ? `，挂了 ${rows.length} 个当事人` : ""}`);
      onCreated(c.id);
    } catch (e) { fail(e); } finally { setBusy(false); }
  }

  const nameOf = (id: number) => parties.find((p) => p.id === id)?.name ?? `主体${id}`;

  return (
    <Section title="新建案件" extra={
      <button className="btn ghost sm" onClick={onCancel}>取消</button>
    }>
      <Notice text={notice.text} kind={notice.kind} onClose={clear} />

      <div className="newcase-grid">
        <label className="small">案件编号
          <input value={code} onChange={(e) => setCode(e.target.value)}
                 placeholder="留空自动生成 AJ2026-0001" />
        </label>
        <label className="small">案件名称 *
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="甲诉乙建设工程施工合同纠纷" />
        </label>
        {/* ★ 案由从 5 条变成了**最高法全表 408 条**，下拉框装不下 ——
            换成可搜索的选择器，而且只有叶子能选（分类那一级法院不认）。 */}
        <label className="small">案由</label>
        <CausePicker causes={causes} value={causeId} onChange={setCauseId} />
        <label className="small">主办律师
          <select value={leadId} onChange={(e) => setLeadId(e.target.value ? Number(e.target.value) : "")}>
            <option value="">我自己</option>
            {users.map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
          </select>
        </label>
        <label className="small">承办团队
          <select value={orgId} onChange={(e) => setOrgId(e.target.value ? Number(e.target.value) : "")}>
            <option value="">不指定</option>
            {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
        </label>
        <label className="small">法院
          <input value={court} onChange={(e) => setCourt(e.target.value)} placeholder="青岛市中级人民法院" />
        </label>
        <label className="small">案号
          <input value={externalNo} onChange={(e) => setExternalNo(e.target.value)} placeholder="(2026)鲁02民初123号" />
        </label>
        <label className="small">标的额
          <input value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="3200000" />
        </label>
        <label className="small">立案日
          <input type="date" value={openedOn} onChange={(e) => setOpenedOn(e.target.value)} />
        </label>
      </div>

      {/* ★ 当事人 */}
      <div className="newcase-block">
        <div className="muted small" style={{ marginBottom: 6 }}>
          <b>当事人</b> —— 利冲检查靠它跨案件比对，文书的原告/被告也从这里填。
          不挂当事人，这两个功能都是空转。
        </div>

        {rows.map((r, i) => (
          <div key={i} className="partyrow">
            <span style={{ flex: 1 }}>{nameOf(r.party_id)}</span>
            <select value={r.role} style={{ width: 120 }}
                    onChange={(e) => setRows((xs) => xs.map((x, j) =>
                      j === i ? { ...x, role: e.target.value } : x))}>
              {Object.entries(PARTY_ROLE_LABEL).map(([k, v]) =>
                <option key={k} value={k}>{v}</option>)}
            </select>
            <select value={r.is_ours ? "1" : "0"} style={{ width: 78 }}
                    onChange={(e) => setRows((xs) => xs.map((x, j) =>
                      j === i ? { ...x, is_ours: e.target.value === "1" } : x))}>
              <option value="1">我方</option>
              <option value="0">对方</option>
            </select>
            <button className="btn ghost sm"
                    onClick={() => setRows((xs) => xs.filter((_, j) => j !== i))}>移除</button>
          </div>
        ))}

        <div className="addparty">
          <select value=""
                  onChange={(e) => {
                    const id = Number(e.target.value);
                    if (id) setRows((r) => [...r, { party_id: id, role: "defendant", is_ours: false }]);
                  }}>
            <option value="">从主体库选…</option>
            {parties.filter((p) => !rows.some((r) => r.party_id === p.id))
                    .map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <input value={newName} onChange={(e) => setNewName(e.target.value)}
                 placeholder="或新建主体，输入名称" />
        </div>

        {/* ★ 「加为我方 / 加为对方」原来只隔 8px，紧挨着 ——
            而这两个**点错的后果完全相反**：我方错成对方，利冲就反了，
            系统会拿自己人去跟自己人查冲突。
            所以：拉开距离、加图标、我方用主色对方用描边，
            并且把名字重复一遍（"把「张三」加为…"）。 */}
        {newName.trim().length >= 2 && (
          <div className="sidepick">
            <span className="muted small">
              把「<b>{newName.trim()}</b>」加为：
            </span>
            <button className="btn" onClick={() => addNewParty(true)}>
              ◤ 我方
            </button>
            <span className="sidepick-gap" />
            <button className="btn secondary" onClick={() => addNewParty(false)}>
              ◥ 对方
            </button>
          </div>
        )}

        {similar.length > 0 && (
          <div className="alert" style={{ marginTop: 8 }}>
            <span className="small">
              <b>主体库里已有相近的</b>，优先选已有的 —— 重复主体会让利冲查不全：
              {similar.map((p) => (
                <button key={p.id} className="btn ghost sm" style={{ marginLeft: 6 }}
                        onClick={() => {
                          setRows((r) => [...r, { party_id: p.id, role: "defendant", is_ours: false }]);
                          setNewName(""); setSimilar([]);
                        }}>{p.name}</button>
              ))}
            </span>
          </div>
        )}
      </div>

      {/* ★ 案情 */}
      <div className="newcase-block">
        <div className="muted small" style={{ marginBottom: 6 }}>
          <b>案情</b> —— 办案流程从这段话里抽要素（竣工日期、应付款日期、欠付金额、
          有无招投标…），<b>写多细流程就跑多准</b>。一句话的案情跑出来会告诉你「缺日期，算不出时限」。
        </div>
        <textarea rows={7} value={summary} onChange={(e) => setSummary(e.target.value)}
                  placeholder={"合同签订时间与价款、竣工时间、有没有组织验收、结算情况、\n约定的付款时间、欠付金额、有无招投标或借资质、对方拒付理由…"} />
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button className="btn" onClick={submit} disabled={busy}>{busy ? "建案中…" : "建案"}</button>
        <button className="btn secondary" onClick={onCancel}>取消</button>
      </div>
    </Section>
  );
}
