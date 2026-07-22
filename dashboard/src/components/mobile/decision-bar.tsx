// Decision guidance bar — simple colored text strip under cards

export function DecisionBar({ 
  text, 
  color = "zinc" 
}: { 
  text: string; 
  color?: "emerald" | "amber" | "red" | "zinc" | "violet";
}) {
  const colors: Record<string, string> = {
    emerald: "border-emerald-800/40 text-emerald-400/70 bg-emerald-500/5",
    amber: "border-amber-800/40 text-amber-400/70 bg-amber-500/5",
    red: "border-red-800/40 text-red-400/70 bg-red-500/5",
    zinc: "border-zinc-800/40 text-zinc-500 bg-zinc-800/20",
    violet: "border-violet-800/40 text-violet-400/70 bg-violet-500/5",
  };
  
  return (
    <div className={`mt-2 px-2.5 py-1.5 rounded-lg border text-[9px] leading-relaxed ${colors[color]}`}>
      💡 {text}
    </div>
  );
}
