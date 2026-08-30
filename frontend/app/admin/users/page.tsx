"use client";

import { useEffect, useMemo, useState } from "react";

import {
  fetchUsers, fetchPositions, fetchUserPositions, fetchOrgUnits,
  fetchUserRoles, fetchAppointments, fetchRoles,
  updatePosition, promote, assignRole, revokeRole,
  type User, type Position, type OrgUnit, type Role,
  type RoleAssignment, type Appointment,
} from "@/lib/api";
import { useAuth, Can } from "@/lib/auth";
import { Badge, Button, DataTable, Field, Notice, Section, Tag, Toggle, type Column, useNotice } from "@/components/ui";
import { LicensePanel, LicenseTag } from "@/components/LicensePanel";

const CATEGORY_LABEL: Record<string, string> = {
  lawyer: "律师", finance: "财务", admin: "行政",
};

export default function UsersPage() {
  const { can } = useAuth();
  const { notice, clear, ok, fail } = useNotice();

  const [users, setUsers] = useState<User[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [userPos, setUserPos] = useState<Record<string, Position | null>>({});
  const [units, setUnits] = useState<OrgUnit[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [selected, setSelected] = useState<User | null>(null);
  const [userRoles, setUserRoles] = useState<RoleAssignment[]>([]);
  const [history, setHistory] = useState<Appointment[]>([]);

  const canManageRole = can("identity.manage_role");
  const canPromote = can("identity.promote");

  function reloadAll() {
    Promise.all([fetchUsers(), fetchPositions(), fetchUserPositions(), fetchOrgUnits()])
      .then(([us, ps, up, ou]) => { setUsers(us); setPositions(ps); setUserPos(up); setUnits(ou); })
      .catch(fail);
    if (canManageRole) fetchRoles().then(setRoles).catch(() => {});
  }
  useEffect(reloadAll, []);

  function openUser(u: User) {
    setSelected(u);
    fetchUserRoles(u.id).then(setUserRoles).catch(fail);
    fetchAppointments(u.id).then(setHistory).catch(fail);
  }

  const posById = useMemo(
    () => Object.fromEntries(positions.map((p) => [p.id, p])), [positions]);
  const roleById = useMemo(
    () => Object.fromEntries(roles.map((r) => [r.id, r])), [roles]);

  async function togglePosition(p: Position, field: keyof Position, value: boolean) {
    try {
      await updatePosition(p.code, { [field]: value } as Partial<Position>);
      ok(`已更新「${p.name}」`);
      reloadAll();
    } catch (e) { fail(e); }
  }

  async function doPromote(code: string) {
    if (!selected) return;
    try {
      await promote(selected.id, code, "管委会决议");
      ok(`已将 ${selected.full_name} 定级为「${positions.find((p) => p.code === code)?.name}」`);
      openUser(selected);
      reloadAll();
    } catch (e) { fail(e); }
  }

  // ---------------------------------------------------------------- 职级表

  const positionColumns: Column<Position>[] = [
    { key: "name", title: "职级", width: 110 },
    { key: "category", title: "大类", width: 70,
      render: (p) => CATEGORY_LABEL[p.category] ?? p.category },
    { key: "rank", title: "级别", width: 60 },
    { key: "is_partner", title: "合伙人", width: 70,
      render: (p) => <Toggle checked={p.is_partner} disabled={!canManageRole}
                             onChange={(v) => togglePosition(p, "is_partner", v)} /> },
    { key: "can_bill", title: "可计时", width: 70,
      render: (p) => <Toggle checked={p.can_bill} disabled={!canManageRole}
                             onChange={(v) => togglePosition(p, "can_bill", v)} /> },
    { key: "can_lead_team", title: "可牵头团队", width: 90,
      render: (p) => <Toggle checked={p.can_lead_team} disabled={!canManageRole}
                             onChange={(v) => togglePosition(p, "can_lead_team", v)} /> },
    { key: "can_handle_case", title: "可独立承办", width: 90,
      render: (p) => <Toggle checked={p.can_handle_case} disabled={!canManageRole}
                             onChange={(v) => togglePosition(p, "can_handle_case", v)} /> },
  ];

  // ---------------------------------------------------------------- 人员表

  const userColumns: Column<User>[] = [
    { key: "full_name", title: "姓名", width: 100 },
    { key: "username", title: "账号", width: 110 },
    { key: "position", title: "职级",
      render: (u) => {
        const p = userPos[String(u.id)];
        return p ? <>{p.name}{p.is_partner && <Badge tone="brand">合伙人</Badge>}</>
                 : <span className="muted">未定级</span>;
      } },
    // ★ 执业状态要在**列表**上看得见，不能点进详情才发现证注销了
    { key: "license", title: "执业", width: 130,
      render: (u) => <LicenseTag lic={{
        status: u.license_status ?? "",
        expired: Boolean(u.license_valid_to &&
                         u.license_valid_to < new Date().toISOString().slice(0, 10)),
      }} /> },
    { key: "is_active", title: "状态", width: 70,
      render: (u) => u.is_active ? "在职" : <span className="muted">停用</span> },
  ];

  return (
    <>
      <Notice text={notice.text} kind={notice.kind} onClose={clear} />

      <Section
        title="职级设置"
        extra={<span className="muted small">
          每个开关都驱动真实业务规则，改了立即生效 —— 这是数据不是代码
        </span>}
      >
        <DataTable columns={positionColumns} rows={positions} />
        {!canManageRole && (
          <p className="muted small">你没有 identity.manage_role，开关只读。</p>
        )}
      </Section>

      <div className="grid aside">
        <div className="stack">
          <Section title="人员">
            <DataTable columns={userColumns} rows={users}
                       onRowClick={openUser} activeId={selected?.id ?? null} />
          </Section>
        </div>

        <div className="stack">
          {!selected && <Section title="人员详情">
            <p className="muted">点左侧任一人员查看详情</p>
          </Section>}

          {selected && (
            <>
              <LicensePanel userId={selected.id} name={selected.full_name}
                            onNotice={(k, t) => k === "ok" ? ok(t) : fail(new Error(t))} />

              <Section title={`${selected.full_name} · 职级履历`}>
                <table>
                  <tbody>
                    {history.map((a) => (
                      <tr key={a.id}>
                        <td>{posById[a.position_id]?.name ?? a.position_id}</td>
                        <td className="muted small">
                          {a.valid_from} ~ {a.valid_to ?? "至今"}
                        </td>
                        <td className="muted small">{a.note}</td>
                      </tr>
                    ))}
                    {history.length === 0 && (
                      <tr><td className="muted">尚未定级</td></tr>
                    )}
                  </tbody>
                </table>
                <Can perm="identity.promote" fallback={
                  <p className="muted small">晋升是管委会职权，你没有 identity.promote。</p>
                }>
                  <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
                    {positions.map((p) => (
                      <button key={p.code} className="btn secondary" onClick={() => doPromote(p.code)}>
                        定级为 {p.name}
                      </button>
                    ))}
                  </div>
                </Can>
              </Section>

              <Section title={`${selected.full_name} · 角色授权`}>
                <table>
                  <tbody>
                    {userRoles.map((a) => (
                      <tr key={a.id}>
                        <td>
                          {roleById[a.role_id]?.name ?? `角色${a.role_id}`}
                          {a.org_unit_id && (
                            <Badge>限 {units.find((u) => u.id === a.org_unit_id)?.name}</Badge>
                          )}
                        </td>
                        <td className="muted small">{a.valid_from} ~ {a.valid_to ?? "至今"}</td>
                        <td style={{ textAlign: "right" }}>
                          {canManageRole && (
                            <button className="link" onClick={async () => {
                              try {
                                await revokeRole(a.id);
                                ok("已收回授权");
                                openUser(selected);
                              } catch (e) { fail(e); }
                            }}>收回</button>
                          )}
                        </td>
                      </tr>
                    ))}
                    {userRoles.length === 0 && <tr><td className="muted">无角色</td></tr>}
                  </tbody>
                </table>
                <Can perm="identity.manage_role" fallback={
                  <p className="muted small">你没有 identity.manage_role，不能改授权。</p>
                }>
                  <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
                    {roles
                      .filter((r) => !userRoles.some((a) => a.role_id === r.id))
                      .map((r) => (
                        <button key={r.code} className="btn secondary" onClick={async () => {
                          try {
                            await assignRole(selected.id, r.code);
                            ok(`已授予「${r.name}」`);
                            openUser(selected);
                          } catch (e) { fail(e); }
                        }}>+ {r.name}</button>
                      ))}
                  </div>
                </Can>
              </Section>
            </>
          )}
        </div>
      </div>
    </>
  );
}
