"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { RiskSlider } from "@/components/risk-widgets";

type Rule = { metric: string; op: string; value: number | boolean | string };

const METRICS: { key: string; label: string; kind: "number" | "boolean" | "choice"; backtestable: boolean; hint: string; choices?: string[] }[] = [
  { key: "momentum_score", label: "Momentum score (0–100)", kind: "number", backtestable: true, hint: "Cross-sectional rank of 6-month return" },
  { key: "price_above_200_day_average", label: "Price above 200-day average", kind: "boolean", backtestable: true, hint: "Long-term trend filter" },
  { key: "price_above_50_day_average", label: "Price above 50-day average", kind: "boolean", backtestable: true, hint: "Medium-term trend filter" },
  { key: "relative_strength", label: "Relative strength vs benchmark (%)", kind: "number", backtestable: true, hint: "63-day return minus benchmark return" },
  { key: "rsi_14", label: "RSI (14)", kind: "number", backtestable: true, hint: "Overbought > 70, oversold < 30" },
  { key: "volatility_30d", label: "Volatility 30d annualized (%)", kind: "number", backtestable: true, hint: "Realized volatility" },
  { key: "volume_confirmation", label: "Volume above 20-day average", kind: "boolean", backtestable: true, hint: "Participation filter" },
  { key: "drawdown_from_entry", label: "Drawdown from entry (%)", kind: "number", backtestable: true, hint: "Exit rules: acts as a stop-loss" },
  { key: "quality_score", label: "Quality score (0–100)", kind: "number", backtestable: false, hint: "Fundamentals quality — evaluated by the research agent" },
  { key: "valuation_risk", label: "Valuation risk", kind: "choice", backtestable: false, hint: "Agent-evaluated", choices: ["low", "moderate", "high", "extreme"] },
  { key: "news_sentiment", label: "News sentiment (−1…1)", kind: "number", backtestable: false, hint: "Agent-evaluated" },
  { key: "thesis_broken", label: "Thesis broken", kind: "boolean", backtestable: false, hint: "Agent-evaluated thesis monitor" },
];

const OPS = [">", ">=", "<", "<=", "==", "!="];
const STEPS = ["Basics", "Universe", "Entry rules", "Exit rules", "Risk limits", "Review"];

function RuleEditor({ rules, setRules, defaults }: {
  rules: Rule[]; setRules: (r: Rule[]) => void; defaults: Rule;
}) {
  function update(i: number, patch: Partial<Rule>) {
    const next = [...rules];
    let r = { ...next[i], ...patch };
    if (patch.metric) {
      const m = METRICS.find((x) => x.key === patch.metric)!;
      r = { ...r, op: "==", value: m.kind === "boolean" ? true : m.kind === "choice" ? m.choices![0] : 50 };
      if (m.kind === "number") r.op = ">";
    }
    next[i] = r;
    setRules(next);
  }
  return (
    <div className="space-y-2">
      {rules.map((r, i) => {
        const m = METRICS.find((x) => x.key === r.metric)!;
        return (
          <div key={i} className="flex flex-wrap items-center gap-2 rounded-md border bg-white p-2">
            <select value={r.metric} onChange={(e) => update(i, { metric: e.target.value })}
              className="border rounded h-9 px-2 text-sm min-w-60">
              {METRICS.map((mm) => (
                <option key={mm.key} value={mm.key}>{mm.label}{mm.backtestable ? "" : " (agent)"}</option>
              ))}
            </select>
            <select value={r.op} onChange={(e) => update(i, { op: e.target.value })}
              className="border rounded h-9 px-2 text-sm">
              {(m.kind === "number" ? OPS : ["==", "!="]).map((o) => <option key={o}>{o}</option>)}
            </select>
            {m.kind === "number" && (
              <Input type="number" step="any" value={String(r.value)} className="w-28"
                onChange={(e) => update(i, { value: parseFloat(e.target.value) || 0 })} />
            )}
            {m.kind === "boolean" && (
              <select value={String(r.value)} onChange={(e) => update(i, { value: e.target.value === "true" })}
                className="border rounded h-9 px-2 text-sm">
                <option value="true">true</option><option value="false">false</option>
              </select>
            )}
            {m.kind === "choice" && (
              <select value={String(r.value)} onChange={(e) => update(i, { value: e.target.value })}
                className="border rounded h-9 px-2 text-sm">
                {m.choices!.map((c) => <option key={c}>{c}</option>)}
              </select>
            )}
            <span className="text-xs text-zinc-500">{m.hint}</span>
            <Badge variant="outline" className={m.backtestable ? "border-emerald-400 text-emerald-700" : "border-amber-400 text-amber-700"}>
              {m.backtestable ? "backtestable" : "agent-evaluated"}
            </Badge>
            <button type="button" onClick={() => setRules(rules.filter((_, j) => j !== i))}
              className="ml-auto text-zinc-400 hover:text-red-600">✕</button>
          </div>
        );
      })}
      <Button type="button" variant="outline" size="sm" onClick={() => setRules([...rules, { ...defaults }])}>
        + Add rule
      </Button>
    </div>
  );
}

export default function StrategyWizard() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // step 1 — basics
  const [name, setName] = useState("");
  const [style, setStyle] = useState("quality growth");
  const [horizon, setHorizon] = useState("3 to 18 months");
  const [description, setDescription] = useState("");
  const [benchmark, setBenchmark] = useState("SPY");
  // step 2 — universe
  const [symbols, setSymbols] = useState("AAPL, MSFT, NVDA, GOOGL, AMZN");
  const [exclusions, setExclusions] = useState("");
  const [classes, setClasses] = useState<string[]>(["stocks", "ETFs"]);
  const [regions, setRegions] = useState("US");
  // steps 3/4 — rules
  const [entryRules, setEntryRules] = useState<Rule[]>([
    { metric: "momentum_score", op: ">", value: 60 },
    { metric: "price_above_200_day_average", op: "==", value: true },
  ]);
  const [exitRules, setExitRules] = useState<Rule[]>([
    { metric: "drawdown_from_entry", op: ">", value: 12 },
  ]);
  // step 5 — risk
  const [risk, setRisk] = useState({
    max_position_weight_pct: 10, max_sector_weight_pct: 30, max_portfolio_drawdown_pct: 15,
    max_daily_loss_pct: 3, max_number_of_positions: 15, max_orders_per_day: 20,
    rebalance_frequency: "weekly",
  });

  function buildTwin() {
    return {
      strategy_name: name,
      universe: {
        asset_classes: classes,
        regions: regions.split(",").map((s) => s.trim()).filter(Boolean),
        symbols: symbols.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean),
        exclusions: exclusions.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean),
      },
      investment_thesis: { style, horizon, description },
      entry_rules: entryRules,
      exit_rules: exitRules,
      risk_management: risk,
      execution: { mode: "paper_trading_only", broker: "sim", human_approval_required: true },
      benchmark,
    };
  }

  const canNext = step === 0 ? name.trim().length > 0 : step === 1 ? symbols.trim().length > 0 : true;

  async function create() {
    setBusy(true); setError("");
    try {
      const s = await api<{ id: number }>("/api/strategies", {
        method: "POST", body: JSON.stringify({ twin: buildTwin(), comment: "created with wizard" }),
      });
      router.push(`/strategies/${s.id}`);
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); setBusy(false); }
  }

  return (
    <div className="max-w-4xl space-y-6">
      <h1 className="text-2xl font-bold">Strategy Builder</h1>
      <div className="flex gap-1">
        {STEPS.map((s, i) => (
          <button key={s} onClick={() => i < step && setStep(i)}
            className={`flex-1 rounded px-2 py-1.5 text-xs font-medium ${
              i === step ? "bg-zinc-900 text-white" : i < step ? "bg-zinc-200 text-zinc-700" : "bg-zinc-100 text-zinc-400"}`}>
            {i + 1}. {s}
          </button>
        ))}
      </div>

      <Card>
        <CardHeader><CardTitle className="text-base">{STEPS[step]}</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          {step === 0 && (
            <div className="grid md:grid-cols-2 gap-4">
              <label className="text-sm md:col-span-2">Strategy name *
                <Input value={name} onChange={(e) => setName(e.target.value)}
                  placeholder="Quality Growth With Risk Control" className="mt-1" /></label>
              <label className="text-sm">Style
                <Input value={style} onChange={(e) => setStyle(e.target.value)} className="mt-1" /></label>
              <label className="text-sm">Time horizon
                <Input value={horizon} onChange={(e) => setHorizon(e.target.value)} className="mt-1" /></label>
              <label className="text-sm md:col-span-2">Investment thesis — what do you believe, and why will it make money?
                <Textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)}
                  className="mt-1" placeholder="e.g. Companies with durable margins and improving momentum outperform over 6-18 months…" /></label>
              <label className="text-sm">Benchmark
                <Input value={benchmark} onChange={(e) => setBenchmark(e.target.value.toUpperCase())} className="mt-1 w-32" /></label>
            </div>
          )}

          {step === 1 && (
            <div className="space-y-4">
              <label className="text-sm block">Symbols you want to trade (comma-separated) *
                <Textarea rows={2} value={symbols} onChange={(e) => setSymbols(e.target.value)} className="mt-1" />
                <span className="text-xs text-zinc-500">This is also the whitelist the risk gateway enforces — orders outside it are blocked.</span>
              </label>
              <label className="text-sm block">Exclusions (never trade these)
                <Input value={exclusions} onChange={(e) => setExclusions(e.target.value)} placeholder="GME, AMC" className="mt-1" /></label>
              <div className="flex gap-6">
                <label className="text-sm">Asset classes
                  <div className="mt-1 flex gap-3">
                    {["stocks", "ETFs"].map((c) => (
                      <label key={c} className="flex items-center gap-1.5">
                        <input type="checkbox" checked={classes.includes(c)}
                          onChange={(e) => setClasses(e.target.checked ? [...classes, c] : classes.filter((x) => x !== c))} />
                        {c}
                      </label>
                    ))}
                  </div>
                </label>
                <label className="text-sm">Regions (comma-separated)
                  <Input value={regions} onChange={(e) => setRegions(e.target.value)} className="mt-1 w-40" /></label>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-2">
              <p className="text-sm text-zinc-600">A stock is a <b>buy candidate</b> only when <b>ALL</b> entry rules hold.</p>
              <RuleEditor rules={entryRules} setRules={setEntryRules}
                defaults={{ metric: "momentum_score", op: ">", value: 60 }} />
            </div>
          )}

          {step === 3 && (
            <div className="space-y-2">
              <p className="text-sm text-zinc-600">A position is <b>flagged for exit</b> when <b>ANY</b> exit rule triggers.</p>
              <RuleEditor rules={exitRules} setRules={setExitRules}
                defaults={{ metric: "drawdown_from_entry", op: ">", value: 12 }} />
            </div>
          )}

          {step === 4 && (
            <div className="grid md:grid-cols-2 gap-x-8 gap-y-5">
              <RiskSlider label="Max position weight" value={risk.max_position_weight_pct}
                onChange={(v) => setRisk({ ...risk, max_position_weight_pct: v })}
                min={1} max={25} hint="single-stock concentration" />
              <RiskSlider label="Max sector weight" value={risk.max_sector_weight_pct}
                onChange={(v) => setRisk({ ...risk, max_sector_weight_pct: v })}
                min={10} max={100} step={5} hint="sector concentration" />
              <RiskSlider label="Max portfolio drawdown" value={risk.max_portfolio_drawdown_pct}
                onChange={(v) => setRisk({ ...risk, max_portfolio_drawdown_pct: v })}
                min={5} max={40} hint="buys blocked beyond this" />
              <RiskSlider label="Max daily loss" value={risk.max_daily_loss_pct}
                onChange={(v) => setRisk({ ...risk, max_daily_loss_pct: v })}
                min={1} max={10} step={0.5} hint="circuit breaker" />
              <RiskSlider label="Max number of positions" value={risk.max_number_of_positions}
                onChange={(v) => setRisk({ ...risk, max_number_of_positions: v })}
                min={1} max={50} unit="" hint="diversification" />
              <RiskSlider label="Max orders per day" value={risk.max_orders_per_day}
                onChange={(v) => setRisk({ ...risk, max_orders_per_day: v })}
                min={1} max={50} unit="" hint="overtrading guard" />
              <p className="md:col-span-2 text-xs text-zinc-500">
                These are hard limits — the deterministic risk gateway blocks any order that would breach them,
                and the monitor raises alerts as you approach them. Green = conservative, red = aggressive.
              </p>
            </div>
          )}

          {step === 5 && (
            <div className="space-y-3">
              <p className="text-sm text-zinc-600">
                Review your Strategy Twin. It is stored as version 1 (immutable) — every later edit creates a new version.
              </p>
              <pre className="rounded-lg bg-zinc-950 text-zinc-100 p-4 text-xs overflow-auto max-h-96">
                {JSON.stringify(buildTwin(), null, 2)}
              </pre>
              <p className="text-xs text-zinc-500">
                Execution is locked to <b>paper_trading_only</b> with <b>human approval required</b> — not editable in MVP 1.
              </p>
            </div>
          )}

          {error && <p className="text-sm text-red-600">{error}</p>}
          <div className="flex justify-between border-t pt-4">
            <Button variant="outline" onClick={() => setStep(Math.max(0, step - 1))} disabled={step === 0}>
              ← Back
            </Button>
            {step < STEPS.length - 1 ? (
              <Button onClick={() => setStep(step + 1)} disabled={!canNext}>Next →</Button>
            ) : (
              <Button onClick={create} disabled={busy} className="bg-emerald-700 hover:bg-emerald-800">
                {busy ? "Creating…" : "Create strategy (v1)"}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
