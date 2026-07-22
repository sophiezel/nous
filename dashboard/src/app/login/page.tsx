"use client";

import { signIn } from "next-auth/react";
import { useState, useEffect, useRef } from "react";
import { getDeviceFingerprint } from "@/lib/fingerprint";
import { Eye, EyeOff, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

export default function LoginPage() {
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [shake, setShake] = useState(false);
  const usernameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    usernameRef.current?.focus();
  }, []);

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    setLoading(true);
    setShake(false);

    const form = new FormData(e.currentTarget);
    const fp = await getDeviceFingerprint();

    const result = await signIn("credentials", {
      username: form.get("username") as string,
      password: form.get("password") as string,
      deviceFingerprint: fp,
      redirect: false,
      callbackUrl: "/",
    });

    if (result?.error) {
      setError("用户名或密码错误");
      setLoading(false);
      setShake(true);
      setTimeout(() => setShake(false), 500);
    } else if (result?.ok) {
      window.location.href = result.url || "/";
    }
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
            "bg-zinc-900/70 backdrop-blur-2xl border border-zinc-800/50 rounded-2xl p-8 shadow-2xl shadow-black/60",
            shake && "animate-[shake_0.4s_ease-in-out]"
          )}
        >
          {/* Logo area */}
          <div className="text-center mb-8">
            <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-emerald-500/10 ring-1 ring-emerald-500/20 mb-5">
              <Sparkles className="w-7 h-7 text-emerald-400" />
            </div>
            <h1 className="text-xl font-bold text-zinc-100 tracking-tight">
              天工
            </h1>
            <p className="text-sm text-zinc-500 mt-1.5">
              量化投研数据平台
            </p>
          </div>

          <form
            onSubmit={handleSubmit}
            className="space-y-4"
            autoComplete="on"
          >
            {/* Username */}
            <input
              ref={usernameRef}
              name="username"
              type="text"
              required
              autoComplete="username"
              placeholder="用户名"
              className="w-full px-4 py-3 bg-zinc-950/80 border border-zinc-800 rounded-xl text-zinc-100 placeholder-zinc-600 text-sm focus:outline-none focus:border-emerald-500/50 focus:ring-2 focus:ring-emerald-500/10 transition-all duration-200"
            />

            {/* Password */}
            <div className="relative">
              <input
                name="password"
                type={showPassword ? "text" : "password"}
                required
                autoComplete="current-password"
                placeholder="密码"
                className="w-full px-4 py-3 pr-11 bg-zinc-950/80 border border-zinc-800 rounded-xl text-zinc-100 placeholder-zinc-600 text-sm focus:outline-none focus:border-emerald-500/50 focus:ring-2 focus:ring-emerald-500/10 transition-all duration-200"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-600 hover:text-zinc-400 transition-colors p-1"
                tabIndex={-1}
              >
                {showPassword ? (
                  <EyeOff className="w-4 h-4" />
                ) : (
                  <Eye className="w-4 h-4" />
                )}
              </button>
            </div>

            {/* Error */}
            {error && (
              <div className="flex items-center gap-2.5 px-3.5 py-2.5 bg-red-500/5 border border-red-500/10 rounded-xl">
                <div className="w-1.5 h-1.5 rounded-full bg-red-400 flex-shrink-0" />
                <p className="text-xs text-red-400">{error}</p>
              </div>
            )}

            {/* Submit */}
            <button
              type="submit"
              disabled={loading}
              className="relative w-full py-3 rounded-xl bg-emerald-500 hover:bg-emerald-400 disabled:opacity-50 text-white font-medium text-sm transition-all duration-200 overflow-hidden group mt-2"
            >
              <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/10 to-transparent -translate-x-full group-hover:translate-x-full transition-transform duration-700" />
              <span className="relative tracking-wider">
                {loading ? "验证中..." : "登 录"}
              </span>
            </button>
          </form>
        </div>

        {/* Footer */}
        <p className="text-center text-[11px] text-zinc-700 mt-6">
          天工 · 仅限授权访问
        </p>
      </div>
    </div>
  );
}
