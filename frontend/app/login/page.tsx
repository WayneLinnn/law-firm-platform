"use client";

/**
 * 登录。
 *
 * ★ 这一页原来用的是 `.wrap` 居中，而 `.wrap` 在设计系统重写时被删了 ——
 *   于是整个表单贴到了屏幕最左边。**改样式要 grep 一遍谁在用旧类名**，
 *   否则最先坏的一定是这种"不在主流程里、平时没人点开"的页面。
 */
import { useState } from "react";
import { useRouter } from "next/navigation";

import { login, setToken, ApiError } from "@/lib/api";
import { Button, Field, Notice } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const [tenant, setTenant] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      const { access_token } = await login(tenant, username, password);
      setToken(access_token);
      // ★ 曾经跳 "/org" —— 那是 Step 3 的首页，早就没了，登进来直接 404。
      //   登录后一律回工作台，别的入口都在导航上。
      router.push("/");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "登录失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="authpage">
      <form className="authbox" onSubmit={submit}>
        <div className="authbrand">
          <span className="nav-mark">律</span>
          <div>
            <h1>康桥律师事务所</h1>
            <p className="muted small" style={{ margin: 0 }}>案件管理平台</p>
          </div>
        </div>

        <Notice text={err} kind="error" onClose={() => setErr("")} />

        <Field label="律所编码">
          <input value={tenant} onChange={(e) => setTenant(e.target.value)}
                 required autoFocus autoComplete="organization"
                 placeholder="kangqiao" />
        </Field>
        <Field label="用户名">
          <input value={username} onChange={(e) => setUsername(e.target.value)}
                 required autoComplete="username" />
        </Field>
        <Field label="密码">
          <input type="password" value={password} autoComplete="current-password"
                 onChange={(e) => setPassword(e.target.value)} required />
        </Field>

        <Button variant="primary" disabled={busy}
                className="authsubmit"
                onClick={(e) => { e.preventDefault(); void submit(e); }}>
          {busy ? "登录中…" : "登录"}
        </Button>
        {/* 表单需要一个真正的 submit 才能响应回车 */}
        <button className="btn" type="submit" hidden aria-hidden tabIndex={-1} />

        <p className="muted small authfoot">
          账号由律所统一分配。忘记密码请找所务管理重置 ——
          <b>系统不做自助找回</b>，律所账号能看到当事人信息。
        </p>
      </form>
    </div>
  );
}
