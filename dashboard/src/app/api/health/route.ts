import { NextResponse } from "next/server";
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const checks: Record<string, string> = {
    dashboard: "ok",
  };

  // Data Service 握手
  try {
    const dsUrl = process.env.DATA_SERVICE_URL || "http://127.0.0.1:3099";
    const res = await fetch(`${dsUrl}/v1/health`, {
      signal: AbortSignal.timeout(3000),
    });
    checks["data_service"] = res.ok ? "ok" : `HTTP ${res.status}`;
  } catch (e: any) {
    checks["data_service"] = `unreachable: ${e.message?.slice(0, 50) || "unknown"}`;
  }

  const allOk = Object.values(checks).every((v) => v === "ok");
  return NextResponse.json(checks, { status: allOk ? 200 : 503 });
}
