"use client";

/**
 * 确认弹窗。
 *
 * ★ 替掉 `window.confirm` —— 那个东西贴在**屏幕最顶上**，
 *   而用户的手和视线都在页面中间的按钮上，很容易没看清就点了确定。
 *   不可逆动作（结案、归档、终止）这么处理是不行的。
 *
 * ★ **危险动作的确定键要用危险色，而且不是默认焦点。**
 *   默认落在「取消」上 —— 一路回车不会误触。
 *
 * ★ 支持「打一遍字才能确认」：真正不可逆的动作（终止案件）
 *   光点一下太便宜了。
 */
import { useEffect, useRef, useState } from "react";

export type ConfirmSpec = {
  title: string;
  /** 说清**会发生什么**，不是"确定吗" */
  body?: React.ReactNode;
  /** 确定键的文字，默认「确定」 */
  okText?: string;
  /** 红色确定键 —— 不可逆动作用 */
  danger?: boolean;
  /** 要求打这个词才能确认。留空表示不要求 */
  typeToConfirm?: string;
  onOk: () => void;
};

export function Confirm({ spec, onClose }: {
  spec: ConfirmSpec | null;
  onClose: () => void;
}) {
  const [typed, setTyped] = useState("");
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    setTyped("");
    if (spec) {
      // ★ 焦点落在**取消**上，不是确定 —— 一路回车不会误触
      const t = setTimeout(() => cancelRef.current?.focus(), 20);
      return () => clearTimeout(t);
    }
  }, [spec]);

  useEffect(() => {
    if (!spec) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [spec, onClose]);

  if (!spec) return null;

  const need = spec.typeToConfirm?.trim();
  const ready = !need || typed.trim() === need;

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true"
           onClick={(e) => e.stopPropagation()}>
        <h3>{spec.title}</h3>
        {spec.body && <div className="modal-body">{spec.body}</div>}

        {need && (
          <div className="field" style={{ marginTop: 12 }}>
            <label>
              确认请输入 <code>{need}</code>
            </label>
            <input value={typed} onChange={(e) => setTyped(e.target.value)}
                   placeholder={need} autoFocus />
          </div>
        )}

        <div className="modal-foot">
          <button ref={cancelRef} className="btn secondary" onClick={onClose}>
            取消
          </button>
          <button className={"btn" + (spec.danger ? " danger" : "")}
                  disabled={!ready}
                  onClick={() => { spec.onOk(); onClose(); }}>
            {spec.okText ?? "确定"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** 用法：`const [ask, confirm] = useConfirm()` —— confirm(spec) 打开，ask 渲染 */
export function useConfirm(): [React.ReactNode, (s: ConfirmSpec) => void] {
  const [spec, setSpec] = useState<ConfirmSpec | null>(null);
  return [
    <Confirm key="confirm" spec={spec} onClose={() => setSpec(null)} />,
    setSpec,
  ];
}
