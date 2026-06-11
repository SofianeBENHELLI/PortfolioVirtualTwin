"use client";

import { Slider } from "@/components/ui/slider";

/** Colored 0-100 risk gauge chip with optional factor tooltip-style breakdown. */
export function RiskGauge({ score, band, compact = false }: {
  score: number | null | undefined; band?: string; compact?: boolean;
}) {
  if (score == null) return <span className="text-xs text-zinc-400">risk —</span>;
  const color = score < 25 ? "bg-emerald-600" : score < 50 ? "bg-lime-600"
    : score < 75 ? "bg-amber-500" : "bg-red-600";
  const label = band ?? (score < 25 ? "low" : score < 50 ? "moderate" : score < 75 ? "elevated" : "high");
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium text-white ${color}`}
      title={`Deterministic risk score: ${score.toFixed(0)}/100 (${label})`}>
      ⚠ {score.toFixed(0)}{!compact && <span className="opacity-80">· {label}</span>}
    </span>
  );
}

export function RiskFactorList({ factors }: { factors: Record<string, { utilization: number; detail: string }> }) {
  return (
    <ul className="space-y-1 text-xs">
      {Object.entries(factors).map(([name, f]) => (
        <li key={name} className="flex items-center gap-2">
          <span className="w-36 shrink-0 text-zinc-500">{name.replaceAll("_", " ")}</span>
          <div className="h-2 w-24 shrink-0 rounded bg-zinc-100">
            <div className={`h-2 rounded ${f.utilization < 0.5 ? "bg-emerald-500" : f.utilization < 0.8 ? "bg-amber-500" : "bg-red-500"}`}
              style={{ width: `${Math.min(100, f.utilization * 100)}%` }} />
          </div>
          <span className="text-zinc-600">{f.detail}</span>
        </li>
      ))}
    </ul>
  );
}

/** Slider for a strategy risk limit, with colored zones (green = conservative). */
export function RiskSlider({ label, value, onChange, min, max, step = 1, unit = "%", inverted = false, hint }: {
  label: string; value: number; onChange: (v: number) => void;
  min: number; max: number; step?: number; unit?: string;
  inverted?: boolean;  // true when LOWER values are riskier (rare)
  hint?: string;
}) {
  const ratio = (value - min) / (max - min);
  const risky = inverted ? 1 - ratio : ratio;
  const color = risky < 0.34 ? "text-emerald-700" : risky < 0.67 ? "text-amber-600" : "text-red-600";
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm">{label}</span>
        <span className={`text-sm font-semibold tabular-nums ${color}`}>{value}{unit}</span>
      </div>
      <Slider value={[value]} min={min} max={max} step={step}
        onValueChange={(v: number | readonly number[]) =>
          onChange(typeof v === "number" ? v : v[0])} />
      <div className="flex justify-between text-[10px] text-zinc-400">
        <span>{inverted ? "aggressive" : "conservative"} {min}{unit}</span>
        {hint && <span className="text-zinc-500">{hint}</span>}
        <span>{inverted ? "conservative" : "aggressive"} {max}{unit}</span>
      </div>
    </div>
  );
}
