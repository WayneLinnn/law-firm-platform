"use client";

/**
 * 当前用户与权限。
 *
 * 前端隐藏按钮**只是体验**，不是安全边界 —— 后端每个接口都有 require_perm，
 * 直接调接口照样 403。所以这里可以放心用，不用担心"藏起来就等于没权限"。
 */
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { fetchMe, getToken, clearToken, type Me } from "./api";

type AuthState = {
  me: Me | null;
  loading: boolean;
  error: string;
  reload: () => void;
  can: (permission: string) => boolean;
  scopeOf: (permission: string) => string;
  logout: () => void;
};

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const reload = useCallback(() => {
    if (!getToken()) {
      router.push("/login");
      return;
    }
    setLoading(true);
    fetchMe()
      .then((m) => { setMe(m); setError(""); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [router]);

  useEffect(reload, [reload]);

  const value: AuthState = {
    me,
    loading,
    error,
    reload,
    can: (p) => !!me && (me.user.is_superuser || me.permissions.includes(p)),
    scopeOf: (p) => (me?.user.is_superuser ? "all" : me?.scopes[p] ?? "self"),
    logout: () => { clearToken(); router.push("/login"); },
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth 必须在 AuthProvider 内使用");
  return ctx;
}

/** 没有该权限就不渲染。用于隐藏按钮、菜单项。 */
export function Can({
  perm, children, fallback = null,
}: { perm: string; children: React.ReactNode; fallback?: React.ReactNode }) {
  const { can } = useAuth();
  return <>{can(perm) ? children : fallback}</>;
}
