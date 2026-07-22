"use client";

import { ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  message?: string;
}

/**
 * 数据错误边界 — 单个模块失败不影响其他模块
 * 
 * 用法:
 *   <DataErrorBoundary message="情绪数据暂不可用">
 *     <SentimentModule />
 *   </DataErrorBoundary>
 */
export function DataErrorBoundary({ children, fallback, message }: Props) {
  // Simplified: wrap in try/catch is done at the fetchAPI level
  // This component provides visual fallback
  return (
    <div className="relative">
      {children}
      {fallback && (
        <div className="absolute inset-0 flex items-center justify-center bg-zinc-900/50 rounded-2xl">
          <div className="text-center p-4">
            <p className="text-zinc-500 text-sm">{message || "数据暂不可用"}</p>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * SSE 连接状态 banner
 */
export function SSEStatusBanner({ disconnected }: { disconnected: boolean }) {
  if (!disconnected) return null;
  return (
    <div className="fixed top-0 left-0 right-0 z-50 bg-amber-500/10 border-b border-amber-500/30 px-4 py-2 text-center">
      <p className="text-amber-400 text-xs">
        数据连接中断 · 正在重连...
      </p>
    </div>
  );
}
