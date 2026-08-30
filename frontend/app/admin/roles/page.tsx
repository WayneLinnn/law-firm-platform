"use client";

import { useEffect, useMemo, useState } from "react";

import {
  fetchRoles, fetchPermissions, setRolePermissions,
  SCOPE_LABEL, type Role, type Permission,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Badge, Button, DataTable, Field, Notice, Section, Tag, type Column, useNotice } from "@/components/ui";

/** 权限点的中文说明。新模块上线后补进来即可，缺了就显示原始 code */
const PERM_LABEL: Record<string, string> = {
  "identity.view": "查看组织、人员、职级",
  "identity.manage_org": "增删改组织",
  "identity.manage_user": "增删改人员、调岗",
  "identity.promote": "晋升定级（管委会职权）",
  "identity.manage_role": "配置角色与权限",
  "identity.manage_wall": "设置隔离墙（利冲隔离）",
  "audit.view": "查看审计日志",
  "hello.view": "示例模块：查看",
  "hello.manage": "示例模块：管理",
};

const MODULE_LABEL: Record<string, string> = {
  identity: "组织与身份", audit: "审计", hello: "示例模块",
};

export default function RolesPage() {
  const { can } = useAuth();
  const { notice, clear, ok, fail } = useNotice();

  const [roles, setRoles] = useState<Role[]>([]);
  const [perms, setPerms] = useState<Permission[]>([]);
  const [selected, setSelected] = useState<Role | null>(null);
  const [draft, setDraft] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);

  const editable = can("identity.manage_role");

  function reload() {
    fetchRoles()
      .then((rs) => {
        setRoles(rs);
        setSelected((cur) => (cur ? rs.find((r) => r.id === cur.id) ?? null : null));
      })
      .catch(fail);
  }
  useEffect(() => {
    reload();
    fetchPermissions().then(setPerms).catch(() => {});   // 无权时静默
  }, []);

  function open(role: Role) {
    setSelected(role);
    setDraft(new Set(role.permissions));
  }

  const byModule = useMemo(() => {
    const out: Record<string, Permission[]> = {};
    for (const p of perms) (out[p.module] ||= []).push(p);
    return out;
  }, [perms]);

  const dirty = useMemo(() => {
    if (!selected) return false;
    const before = new Set(selected.permissions);
    return before.size !== draft.size || [...draft].some((p) => !before.has(p));
  }, [selected, draft]);

  async function save() {
    if (!selected) return;
    setSaving(true);
    try {
      await setRolePermissions(selected.code, [...draft]);
      ok(`已保存「${selected.name}」的权限`);
      reload();
    } catch (e) { fail(e); } finally { setSaving(false); }
  }

  const columns: Column<Role>[] = [
    { key: "name", title: "角色", width: 120 },
    { key: "data_scope", title: "数据范围", width: 120,
      render: (r) => <Badge tone="brand">{SCOPE_LABEL[r.data_scope] ?? r.data_scope}</Badge> },
    { key: "permissions", title: "权限数", width: 70,
      render: (r) => r.permissions.length },
    { key: "description", title: "说明",
      render: (r) => <span className="muted small">{r.description}</span> },
  ];

  return (
    <>
      <Notice text={notice.text} kind={notice.kind} onClose={clear} />

      <Section title="角色" extra={
        <span className="muted small">
          角色 = 一组权限点 + 一个数据范围。业务代码只判断权限点，不判断角色名
        </span>
      }>
        <DataTable columns={columns} rows={roles} onRowClick={open}
                   activeId={selected?.id ?? null} />
      </Section>

      {selected && (
        <Section
          title={`「${selected.name}」的权限点`}
          extra={editable ? (
            <div style={{ display: "flex", gap: 8 }}>
              {dirty && <button className="btn secondary" onClick={() => open(selected)}>撤销</button>}
              <button className="btn" disabled={!dirty || saving} onClick={save}>
                {saving ? "保存中…" : "保存"}
              </button>
            </div>
          ) : <span className="muted small">只读：你没有 identity.manage_role</span>}
        >
          <p className="muted small" style={{ marginTop: 0 }}>
            权限点由各模块在 manifest 里声明 —— <strong>新增模块会自动出现在这里</strong>，
            不用改权限中心。数据范围「{SCOPE_LABEL[selected.data_scope]}」决定这些权限能看到哪些行。
          </p>

          {Object.entries(byModule).map(([mod, items]) => (
            <div key={mod} style={{ marginBottom: 14 }}>
              <div className="muted small" style={{ marginBottom: 6 }}>
                {MODULE_LABEL[mod] ?? mod}
              </div>
              <div className="perm-grid">
                {items.map((p) => (
                  <label key={p.code}
                         className={"perm-item" + (editable ? "" : " readonly")}>
                    <input
                      type="checkbox"
                      disabled={!editable}
                      checked={draft.has(p.code)}
                      onChange={(e) => {
                        const next = new Set(draft);
                        e.target.checked ? next.add(p.code) : next.delete(p.code);
                        setDraft(next);
                      }}
                    />
                    <span title={p.code}>{PERM_LABEL[p.code] ?? p.code}</span>
                  </label>
                ))}
              </div>
            </div>
          ))}

          {perms.length === 0 && (
            <p className="muted">无法读取权限点清单（需要 identity.manage_role）。</p>
          )}
        </Section>
      )}
    </>
  );
}
