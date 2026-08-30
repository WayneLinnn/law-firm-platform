"use client";

/**
 * 执业资格。
 *
 * ★ **这是律所独有的东西，一般公司的人事系统里没有。**
 *   一个人能不能办案，不取决于所里给他什么角色 ——
 *   取决于司法行政机关发没发证、证还有没有效。
 *   所里能把谁提成合伙人，但提不出一张执业证。
 *
 * ★ 三档而不是"有证/没证"：**实习律师不能独立承办、不能单独出庭。**
 *   做成布尔就表达不了这件事，然后实习律师会被挂成主办。
 *
 * ★ 停止执业、已注销的人**不停用账号** —— 他办过的案子要留痕，
 *   账号一停，历史记录里全变成"未知用户"。用状态标记，不删人。
 */
import { useCallback, useEffect, useState } from "react";

import {
  fetchLicense, setLicense, LICENSE_LABEL, type License,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Section, Tag } from "@/components/ui";

const OPTIONS = ["", "practising", "intern", "suspended", "revoked"];

export function LicensePanel({ userId, name, onNotice }: {
  userId: number;
  name: string;
  onNotice: (kind: "ok" | "error", text: string) => void;
}) {
  const { can } = useAuth();
  const editable = can("identity.manage");

  const [lic, setLic] = useState<License | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    license_no: "", status: "", licensed_on: "", valid_to: "", firm: "",
  });

  const load = useCallback(async () => {
    try {
      const l = await fetchLicense(userId);
      setLic(l);
      setForm({
        license_no: l.license_no, status: l.status,
        licensed_on: l.licensed_on, valid_to: l.valid_to, firm: l.firm,
      });
    } catch { setLic(null); }
  }, [userId]);

  useEffect(() => { void load(); }, [load]);

  async function save() {
    setBusy(true);
    try {
      const l = await setLicense(userId, {
        license_no: form.license_no,
        status: form.status,
        licensed_on: form.licensed_on || null,
        valid_to: form.valid_to || null,
        firm: form.firm,
      });
      setLic(l);
      onNotice("ok", `已更新 ${name} 的执业信息`);
    } catch (e) {
      onNotice("error", e instanceof Error ? e.message : "保存失败");
    } finally { setBusy(false); }
  }

  return (
    <Section title={`${name} · 执业资格`} extra={
      lic && <LicenseTag lic={lic} />
    }>
      {/* ★ 有问题就顶在最上面。一个"执业中"的绿标底下压着一张三年前
          就过期的证，比什么都不显示更糟 —— 它让人以为已经查过了。 */}
      {lic?.note && (
        <p className={lic.status === "suspended" || lic.status === "revoked"
                      ? "alert bad" : "alert"}>
          {lic.note}
        </p>
      )}

      {!editable ? (
        <table>
          <tbody>
            <tr><th style={{ width: 90 }}>执业证号</th>
                <td>{lic?.license_no || <span className="muted">未登记</span>}</td></tr>
            <tr><th>状态</th><td>{lic?.status_label ?? "—"}</td></tr>
            <tr><th>准予执业</th><td>{lic?.licensed_on || "—"}</td></tr>
            <tr><th>有效期至</th>
                <td className={lic?.expired ? "bad" : undefined}>
                  {lic?.valid_to || "—"}</td></tr>
            <tr><th>执业机构</th><td>{lic?.firm || "—"}</td></tr>
          </tbody>
        </table>
      ) : (
        <>
          <label>执业证号</label>
          <input value={form.license_no}
                 onChange={(e) => setForm({ ...form, license_no: e.target.value })}
                 placeholder="如 13101202011234567" />

          <label>执业状态</label>
          <select value={form.status}
                  onChange={(e) => setForm({ ...form, status: e.target.value })}>
            {OPTIONS.map((o) => (
              <option key={o} value={o}>{LICENSE_LABEL[o]}</option>
            ))}
          </select>
          {form.status === "intern" && (
            <p className="muted small">
              实习律师<b>不能独立承办案件、不能单独出庭</b>。
              系统里靠职级的「可独立承办」开关拦，这里只是登记。
            </p>
          )}
          {(form.status === "suspended" || form.status === "revoked") && (
            <p className="muted small">
              保存后此人<b>不能再担任新案件的主办律师</b>。
              已办的案子不动 —— 历史要留痕。
            </p>
          )}

          <label>准予执业之日</label>
          <input type="date" value={form.licensed_on}
                 onChange={(e) => setForm({ ...form, licensed_on: e.target.value })} />

          <label>有效期至（年检）</label>
          <input type="date" value={form.valid_to}
                 onChange={(e) => setForm({ ...form, valid_to: e.target.value })} />
          <p className="muted small">
            过期<b>不会自动停人</b> —— 年检晚办几天很常见，把案子卡死没道理。
            这里只会显眼地提示去核实。
          </p>

          <label>执业机构</label>
          <input value={form.firm}
                 onChange={(e) => setForm({ ...form, firm: e.target.value })}
                 placeholder="换所过来的人，这里和本所不一致就要查" />

          <div style={{ marginTop: 12 }}>
            <button className="btn" disabled={busy} onClick={() => void save()}>保存</button>
          </div>
        </>
      )}
    </Section>
  );
}

/** 列表里也要看得见 —— 不能点进详情才发现这人的证注销了 */
export function LicenseTag({ lic }: { lic: License | Partial<License> }) {
  const st = lic.status ?? "";
  if (!st) return <span className="muted small">未登记执业信息</span>;
  const tone = st === "practising" ? (lic.expired ? "warn" : "ok")
             : st === "intern" ? "warn" : "bad";
  return (
    <>
      <Tag tone={tone}>{LICENSE_LABEL[st] ?? st}</Tag>
      {lic.expired && <Tag tone="warn">证已过期</Tag>}
    </>
  );
}
