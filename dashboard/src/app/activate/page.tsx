"use client";

import { useState, useEffect, useRef } from "react";
import { Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

type Step = "code" | "done";

export default function ActivatePage() {
  const [step, setStep] = useState<Step>("code");
  const [inviteCode, setInviteCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [shake, setShake] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  async function submitCode(e: React.FormEvent) {
    e.preventDefault();
    const code = inviteCode.replace(/\s/g, "").toUpperCase();
    if (code.length < 10) {
      setError("请输入完整邀请码");
      triggerShake();
      return;
    }
    setError("");
    setLoading(true);
    try {
      const { getDeviceFingerprint } = await import("@/lib/fingerprint");
      const fp = await getDeviceFingerprint();

      const res = await fetch("/api/activate/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Device-Fingerprint": fp },
        body: JSON.stringify({ code }),
      });
      const data = await res.json();
      if (data.ok) {
        setStep("done");
        setTimeout(() => {
          window.location.href =
            new URLSearchParams(window.location.search).get("callbackUrl") || "/";
        }, 1500);
      } else {
        setError(data.error || "邀请码无效");
        triggerShake();
      }
    } catch {
      setError("网络错误，请重试");
      triggerShake();
    } finally {
      setLoading(false);
    }
  }

  function triggerShake() {
    setShake(true);
    setTimeout(() => setShake(false), 500);
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-zinc-950 relative overflow-hidden">
      {/* Background grid */}
      <div
        className="absolute inset-0 opacity-[0.025]"
        style={{
          backgroundImage: `radial-gradient(circle, #10b981 1px, transparent 1px)`,
          backgroundSize: "26px 26px",
        }}
      />
      {/* Top glow */}
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[640px] h-[320px] bg-emerald-500/[0.04] blur-[140px] rounded-full pointer-events-none" />

      <div className="w-full max-w-sm mx-4 relative z-10">
        {/* Card */}
        <div
          className={cn(
            "bg-zinc-900/70 backdrop-blur-2xl border border-zinc-800/50 rounded-2xl p-8 shadow-2xl shadow-black/60 transition-all duration-300",
            shake && "animate-[shake_0.4s_ease-in-out]"
          )}
        >
          {/* Logo */}
          <div className="text-center mb-8">
            <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-emerald-500/10 ring-1 ring-emerald-500/20 mb-5">
              <Sparkles className="w-7 h-7 text-emerald-400" />
            </div>
            <h1 className="text-xl font-bold text-zinc-100 tracking-tight">
              天工
            </h1>
            <p className="text-sm text-zinc-500 mt-1.5">
              {step === "done" ? "激活成功" : "请输入邀请码"}
            </p>
          </div>

          {step === "code" && (
            <form onSubmit={submitCode} className="space-y-4">
              <input
                ref={inputRef}
                type="text"
                value={inviteCode}
                onChange={(e) => setInviteCode(e.target.value.toUpperCase())}
                placeholder="TG-SUPER-XXXXXXXXXXXX"
                autoComplete="off"
                className="w-full px-4 py-3 bg-zinc-950/80 border border-zinc-800 rounded-xl text-zinc-100 placeholder-zinc-600 text-sm font-mono tracking-wider text-center focus:outline-none focus:border-emerald-500/50 focus:ring-2 focus:ring-emerald-500/10 transition-all duration-200"
              />
              {error && <ErrorBar msg={error} />}
              <SubmitButton loading={loading} label="激活" />
            </form>
          )}

          {step === "done" && (
            <div className="text-center space-y-3 py-4">
              <div className="w-12 h-12 rounded-full bg-emerald-500/20 flex items-center justify-center mx-auto">
                <svg className="w-6 h-6 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <p className="text-sm text-emerald-400 font-medium">设备已激活</p>
              <p className="text-xs text-zinc-500">正在跳转...</p>
            </div>
          )}
        </div>

        <p className="text-center text-[11px] text-zinc-700 mt-6">
          天工 · 仅限授权访问
        </p>
      </div>
    </div>
  );
}

function ErrorBar({ msg }: { msg: string }) {
  return (
    <div className="flex items-center gap-2.5 px-3.5 py-2.5 bg-red-500/5 border border-red-500/10 rounded-xl">
      <div className="w-1.5 h-1.5 rounded-full bg-red-400 flex-shrink-0" />
      <p className="text-xs text-red-400">{msg}</p>
    </div>
  );
}

function SubmitButton({ loading, label }: { loading: boolean; label: string }) {
  return (
    <button
      type="submit"
      disabled={loading}
      className="relative w-full py-3 rounded-xl bg-emerald-500 hover:bg-emerald-400 disabled:opacity-50 text-white font-medium text-sm transition-all duration-200 overflow-hidden group mt-2"
    >
      <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/10 to-transparent -translate-x-full group-hover:translate-x-full transition-transform duration-700" />
      <span className="relative tracking-wider">
        {loading ? "处理中..." : label}
      </span>
    </button>
  );
}
