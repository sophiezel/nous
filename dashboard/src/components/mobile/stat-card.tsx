import { cn } from "@/lib/utils";
import { ReactNode } from "react";

interface StatCardProps {
  icon: ReactNode;
  label: string;
  value: string | number;
  color?: "emerald" | "amber" | "violet" | "blue" | "rose";
  className?: string;
}

const colorMap = {
  emerald: "from-emerald-500/10 to-emerald-500/5 border-emerald-500/20 text-emerald-400",
  amber: "from-amber-500/10 to-amber-500/5 border-amber-500/20 text-amber-400",
  violet: "from-violet-500/10 to-violet-500/5 border-violet-500/20 text-violet-400",
  blue: "from-blue-500/10 to-blue-500/5 border-blue-500/20 text-blue-400",
  rose: "from-rose-500/10 to-rose-500/5 border-rose-500/20 text-rose-400",
};

export function StatCard({
  icon,
  label,
  value,
  color = "emerald",
  className,
}: StatCardProps) {
  return (
    <div
      className={cn(
        "rounded-xl bg-gradient-to-br border p-3",
        colorMap[color],
        className
      )}
    >
      <div className="flex items-center gap-1.5 mb-1.5">
        <span className="opacity-70">{icon}</span>
        <span className="text-[10px] font-medium opacity-70">{label}</span>
      </div>
      <p className="text-lg font-bold tabular-nums">{value}</p>
    </div>
  );
}
