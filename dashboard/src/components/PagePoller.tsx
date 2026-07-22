"use client";

import { useRouter } from "next/navigation";
import { AutoRefresh } from "./AutoRefresh";

/**
 * 页面轮询触发器 — 按 cron 窗口自动 router.refresh()
 * 
 * 原理: router.refresh() 触发 Next.js 重新渲染当前路由的 RSC,
 * 从而重新执行 fetchAPI() 获取最新数据。不刷新浏览器, 用户无感。
 * 
 * 用法: 在任意 Server Component 页面底部加一行:
 *   <PagePoller pollKey="sentiment" />
 */
export function PagePoller({ pollKey }: { pollKey: string }) {
  const router = useRouter();

  return (
    <AutoRefresh
      pollKey={pollKey}
      onPoll={async () => {
        router.refresh();
      }}
    />
  );
}
