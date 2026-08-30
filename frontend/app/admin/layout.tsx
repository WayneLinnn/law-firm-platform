"use client";

/**
 * 所务管理。
 *
 * ★ **和业务区共用同一套骨架**（`.app / .nav / .main`），
 *   只是导航内容不同、品牌块标成红色。
 *
 *   原来这里是另起一套（`.top` / `.admin-body` / `.admin-nav`）——
 *   两套布局意味着两套要维护的样式，而且**两边会慢慢长歪成两个产品**。
 *   用户说的"太散了"，一部分就是这个。
 *
 * ★ 但**边界感要留着**：进了这里，品牌块变红、顶部有一条提示。
 *   所务管理改的是组织、权限、字典 —— 改错了影响的是所有人，
 *   不能让人以为自己还在日常页面里随手点。
 */
import Link from "next/link";
import { usePathname } from "next/navigation";

import { AuthProvider, useAuth } from "@/lib/auth";

const NAV = [
  { href: "/admin/org", label: "组织架构", ico: "◰", perm: "identity.view" },
  { href: "/admin/users", label: "人员与职级", ico: "◱", perm: "identity.view" },
  { href: "/admin/roles", label: "角色与权限", ico: "◳", perm: "identity.manage_role" },
  { href: "/admin/causes", label: "案由字典", ico: "◲", perm: "case.view" },
];

const TITLES: Record<string, string> = {
  "/admin/org": "组织架构",
  "/admin/users": "人员与职级",
  "/admin/roles": "角色与权限",
  "/admin/causes": "案由字典",
};

function Shell({ children }: { children: React.ReactNode }) {
  const { me, loading, error, can, logout } = useAuth();
  const path = usePathname();

  if (loading) return <div className="content muted">加载中…</div>;
  if (error) {
    return <div className="content"><div className="alert bad">{error}</div></div>;
  }
  if (!me) return null;

  const items = NAV.filter((n) => can(n.perm));
  if (items.length === 0) {
    return (
      <div className="content">
        <div className="alert bad">你没有任何所务管理权限。</div>
        <Link href="/" className="btn secondary">返回工作台</Link>
      </div>
    );
  }

  return (
    <div className="app admin">
      <aside className="nav">
        <div className="nav-brand">
          <span className="nav-mark">管</span>
          <span>所务管理</span>
        </div>

        <nav className="nav-body">
          <div className="nav-group">配置</div>
          {items.map((n) => (
            <Link key={n.href} href={n.href}
                  className={path === n.href ? "active" : ""}>
              <span className="nav-ico">{n.ico}</span>
              <span className="grow truncate">{n.label}</span>
            </Link>
          ))}
        </nav>

        <div className="nav-foot">
          <div className="nav-me">
            <span className="nav-avatar">{me.user.full_name.slice(0, 1)}</span>
            <div className="nav-me-txt grow">
              <b>{me.user.full_name}</b>
              <span>{me.position?.name ?? "未定级"}</span>
            </div>
          </div>
          <div className="row tight">
            <Link href="/" className="btn ghost sm grow">← 回业务区</Link>
            <button className="btn ghost sm" onClick={logout}>退出</button>
          </div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <h1>{TITLES[path] ?? "所务管理"}</h1>
          {/* ★ 边界感：这里改的东西影响所有人 */}
          <span className="topbar-sub">
            改的是组织、权限、字典 —— <b>影响全所</b>
          </span>
        </header>
        <div className="content">{children}</div>
      </div>
    </div>
  );
}

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      <Shell>{children}</Shell>
    </AuthProvider>
  );
}
