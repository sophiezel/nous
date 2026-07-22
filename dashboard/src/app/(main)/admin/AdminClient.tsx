"use client";

import { useState } from "react";
import type { User } from "@/lib/db-auth";

const ROLE_OPTIONS = [
  { value: "guest", label: "待激活" },
  { value: "member", label: "会员" },
  { value: "vip", label: "VIP" },
];

const TTL_OPTIONS = [
  { value: 5, label: "5 分钟" },
  { value: 15, label: "15 分钟" },
  { value: 30, label: "30 分钟" },
  { value: 60, label: "1 小时" },
  { value: 120, label: "2 小时" },
];

export function AdminClient({ users: initialUsers }: { users: User[] }) {
  const [users, setUsers] = useState(initialUsers);
  const [inviteModal, setInviteModal] = useState<{ phone: string; initialRole: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);

  async function changeRole(id: number, role: string) {
    const res = await fetch("/api/admin/users", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, role }),
    });
    if (res.ok) {
      setUsers(users.map(u => u.id === id ? { ...u, role: role as User["role"] } : u));
    }
  }

  async function deleteUser(id: number) {
    const res = await fetch("/api/admin/users", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    if (res.ok) {
      setUsers(users.map(u => u.id === id ? { ...u, status: "disabled" as const } : u));
      setConfirmDelete(null);
    }
  }

  async function unbindDevice(id: number) {
    const res = await fetch("/api/admin/users/device", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    if (res.ok) {
      setUsers(users.map(u => u.id === id ? { ...u, device_fps: "[]" } : u));
    }
  }

  function maskPhone(phone: string) {
    if (phone === "admin") return "admin";
    return phone.slice(0, 3) + "****" + phone.slice(-4);
  }

  function deviceCount(fps: string) {
    try { return JSON.parse(fps).length; } catch { return 0; }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-zinc-100">用户管理</h1>
          <p className="text-xs text-zinc-500 mt-1">管理所有用户权限与设备</p>
        </div>
        <button
          onClick={() => setInviteModal({ phone: "", initialRole: "member" })}
          className="px-4 py-2 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-white text-sm font-medium transition-colors"
        >
          + 邀请用户
        </button>
      </div>

      {/* User table */}
      <div className="border border-zinc-800 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-zinc-900/50 border-b border-zinc-800">
              <th className="text-left px-4 py-3 text-zinc-500 font-medium text-xs">ID</th>
              <th className="text-left px-4 py-3 text-zinc-500 font-medium text-xs">手机号</th>
              <th className="text-left px-4 py-3 text-zinc-500 font-medium text-xs">角色</th>
              <th className="text-left px-4 py-3 text-zinc-500 font-medium text-xs">设备</th>
              <th className="text-left px-4 py-3 text-zinc-500 font-medium text-xs">状态</th>
              <th className="text-right px-4 py-3 text-zinc-500 font-medium text-xs">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/50">
            {users.map((u) => (
              <tr key={u.id} className="hover:bg-zinc-900/30 transition-colors">
                <td className="px-4 py-3 text-zinc-400 tabular-nums">{u.id}</td>
                <td className="px-4 py-3 text-zinc-200 font-mono text-xs">
                  {maskPhone(u.phone)}
                </td>
                <td className="px-4 py-3">
                  {u.role === "super_admin" ? (
                    <span className="text-xs px-2 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/20">
                      超管
                    </span>
                  ) : (
                    <select
                      value={u.role}
                      onChange={(e) => changeRole(u.id, e.target.value)}
                      className="text-xs px-2 py-1 rounded bg-zinc-800 border border-zinc-700 text-zinc-300 focus:outline-none focus:border-emerald-500/50"
                    >
                      {ROLE_OPTIONS.map((r) => (
                        <option key={r.value} value={r.value}>{r.label}</option>
                      ))}
                    </select>
                  )}
                </td>
                <td className="px-4 py-3 text-zinc-400 tabular-nums text-xs">
                  {deviceCount(u.device_fps)} 台
                </td>
                <td className="px-4 py-3">
                  <span className={`text-xs px-2 py-0.5 rounded ${
                    u.status === "active" ? "bg-emerald-500/10 text-emerald-400" :
                    u.status === "disabled" ? "bg-red-500/10 text-red-400" :
                    "bg-zinc-800 text-zinc-500"
                  }`}>
                    {u.status === "active" ? "激活" : u.status === "disabled" ? "禁用" : "待激活"}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  {u.role !== "super_admin" && u.status !== "disabled" && (
                    <div className="flex items-center justify-end gap-2">
                      {u.status === "pending" && (
                        <button
                          onClick={() => setInviteModal({ phone: u.phone, initialRole: u.role })}
                          className="text-xs text-emerald-400 hover:text-emerald-300 transition-colors"
                        >
                          生成邀请码
                        </button>
                      )}
                      {deviceCount(u.device_fps) > 0 && (
                        <button
                          onClick={() => unbindDevice(u.id)}
                          className="text-xs text-zinc-500 hover:text-zinc-400 transition-colors"
                        >
                          解绑
                        </button>
                      )}
                      <button
                        onClick={() => setConfirmDelete(u.id)}
                        className="text-xs text-red-500 hover:text-red-400 transition-colors"
                      >
                        删除
                      </button>
                    </div>
                  )}
                  {u.role === "super_admin" && (
                    <span className="text-xs text-zinc-600">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Invite Modal */}
      {inviteModal && (
        <InviteModal
          phone={inviteModal.phone}
          initialRole={inviteModal.initialRole}
          onClose={() => setInviteModal(null)}
        />
      )}

      {/* Delete Confirmation */}
      {confirmDelete !== null && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-6 w-80 shadow-2xl">
            <h3 className="text-sm font-medium text-zinc-100 mb-2">确认删除</h3>
            <p className="text-xs text-zinc-500 mb-5">
              此操作将禁用该用户账户，不可恢复。
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setConfirmDelete(null)}
                className="px-4 py-2 rounded-lg text-xs text-zinc-400 hover:text-zinc-200 transition-colors"
              >
                取消
              </button>
              <button
                onClick={() => deleteUser(confirmDelete)}
                className="px-4 py-2 rounded-lg bg-red-500 hover:bg-red-400 text-white text-xs font-medium transition-colors"
              >
                确认删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function InviteModal({
  phone: initPhone,
  initialRole,
  onClose,
}: {
  phone: string;
  initialRole: string;
  onClose: () => void;
}) {
  const [phone, setPhone] = useState(initPhone);
  const [role, setRole] = useState(initialRole);
  const [ttl, setTtl] = useState(30);
  const [code, setCode] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const [loading, setLoading] = useState(false);
  const [generated, setGenerated] = useState(false);

  async function generate() {
    if (!phone || !/^\d{11}$/.test(phone)) {
      alert("请输入11位手机号");
      return;
    }
    setLoading(true);
    const res = await fetch("/api/admin/invite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone, role, ttl_minutes: ttl }),
    });
    const data = await res.json();
    if (data.code) {
      setCode(data.code);
      setExpiresAt(data.expires_at);
      setGenerated(true);
    }
    setLoading(false);
  }

  async function copyCode() {
    await navigator.clipboard.writeText(code);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-6 w-96 shadow-2xl">
        <h3 className="text-sm font-medium text-zinc-100 mb-4">
          {initPhone ? "生成邀请码" : "邀请新用户"}
        </h3>

        {!initPhone && (
          <div className="mb-3">
            <label className="text-xs text-zinc-500 block mb-1">手机号</label>
            <input
              type="tel"
              maxLength={11}
              value={phone}
              onChange={(e) => setPhone(e.target.value.replace(/\D/g, ""))}
              placeholder="输入手机号"
              className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm placeholder-zinc-600 focus:outline-none focus:border-emerald-500/50"
            />
          </div>
        )}

        <div className="flex gap-3 mb-4">
          <div className="flex-1">
            <label className="text-xs text-zinc-500 block mb-1">角色</label>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-300 text-sm focus:outline-none focus:border-emerald-500/50"
            >
              <option value="member">会员</option>
              <option value="vip">VIP</option>
            </select>
          </div>
          <div className="flex-1">
            <label className="text-xs text-zinc-500 block mb-1">有效期</label>
            <select
              value={ttl}
              onChange={(e) => setTtl(Number(e.target.value))}
              className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-300 text-sm focus:outline-none focus:border-emerald-500/50"
            >
              {TTL_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
        </div>

        {generated ? (
          <div className="space-y-3">
            <div className="px-4 py-3 bg-emerald-500/5 border border-emerald-500/10 rounded-xl text-center">
              <p className="text-xs text-zinc-500 mb-1">邀请码</p>
              <p className="text-lg font-mono font-bold text-emerald-400 tracking-wider">
                {code}
              </p>
              <p className="text-[10px] text-zinc-600 mt-1">
                过期: {expiresAt ? new Date(expiresAt + "Z").toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : ""}
              </p>
            </div>
            <div className="flex gap-3">
              <button
                onClick={copyCode}
                className="flex-1 py-2 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-white text-xs font-medium transition-colors"
              >
                复制
              </button>
              <button
                onClick={onClose}
                className="flex-1 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-400 text-xs font-medium transition-colors"
              >
                关闭
              </button>
            </div>
          </div>
        ) : (
          <div className="flex gap-3">
            <button
              onClick={generate}
              disabled={loading}
              className="flex-1 py-2 rounded-lg bg-emerald-500 hover:bg-emerald-400 disabled:opacity-50 text-white text-xs font-medium transition-colors"
            >
              {loading ? "生成中..." : "生成邀请码"}
            </button>
            <button
              onClick={onClose}
              className="flex-1 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-400 text-xs font-medium transition-colors"
            >
              取消
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
