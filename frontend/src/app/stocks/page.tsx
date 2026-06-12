"use client";

import { useCallback, useEffect, useState } from "react";
import { api, fmtMoney2 } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

type PillarScores = {
  fundamental: number; technical: number; industry: number; sector_macro: number; composite: number;
};
type CaseReport = {
  rating: string; conviction: number; thesis: string;
  pillar_scores: PillarScores; pillar_notes: string[];
  target_price_12m: string; catalysts: string[]; steelman_rebuttals: string[];
  risks: string[]; invalidation: string; data_gaps: string;
};
type ScorecardRow = { pillar: string; bull_score: number; bear_score: number; edge: string; why: string };
type JudgeReport = {
  action: string; conviction: number; verdict_summary: string;
  scorecard: ScorecardRow[]; bull_composite: number; bear_composite: number;
  horizon: string; sizing_guidance: string;
  catalyst_skew_0_6m: string; catalyst_skew_6_18m: string;
  strongest_bull: string[]; strongest_bear: string[];
  material_omissions: string[]; invalidation_triggers: string[]; reevaluation_date: string;
};
type Signal = {
  signal_strength: number; action?: string; rating?: string | null;
  thesis: string; key_points: string[];
  invalidation: string; created_at: string;
  report?: (CaseReport & Partial<JudgeReport>) | (JudgeReport & Partial<CaseReport>) | null;
};
type Stock = {
  symbol: string; added_at: string; price: number | null; data_as_of: string | null;
  indicators: Record<string, number | boolean>;
  fundamentals: Record<string, string | number>;
  bull: Signal | null; bear: Signal | null; judge: Signal | null;
};

const FUND_LABELS: [string, string][] = [
  ["sector", "Sector"], ["industry", "Industry"], ["marketCap", "Market cap"],
  ["trailingPE", "P/E (trailing)"], ["forwardPE", "P/E (forward)"], ["priceToBook", "P/B"],
  ["revenueGrowth", "Revenue growth"], ["earningsGrowth", "Earnings growth"],
  ["profitMargins", "Profit margin"], ["grossMargins", "Gross margin"],
  ["debtToEquity", "Debt/Equity"], ["dividendYield", "Dividend yield"], ["beta", "Beta"],
  ["fiftyTwoWeekHigh", "52w high"], ["fiftyTwoWeekLow", "52w low"],
  ["targetMeanPrice", "Analyst target"], ["recommendationKey", "Analyst view"],
];

const IND_LABELS: [string, string][] = [
  ["momentum_6m_return_pct", "6-month return"], ["relative_strength", "Rel. strength vs SPY (63d)"],
  ["rsi_14", "RSI (14)"], ["volatility_30d", "Volatility 30d (ann.)"],
  ["price_above_200_day_average", "Above 200d average"],
  ["price_above_50_day_average", "Above 50d average"],
  ["volume_confirmation", "Volume above 20d avg"],
];

function fmtVal(key: string, v: string | number | boolean): string {
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "string") return v;
  if (key === "marketCap" || key === "freeCashflow")
    return v >= 1e12 ? `$${(v / 1e12).toFixed(2)}T` : v >= 1e9 ? `$${(v / 1e9).toFixed(1)}B` : `$${(v / 1e6).toFixed(0)}M`;
  if (["revenueGrowth", "earningsGrowth", "profitMargins", "grossMargins", "dividendYield"].includes(key))
    return `${(v * 100).toFixed(1)}%`;
  if (key.includes("pct") || key === "relative_strength" || key === "volatility_30d")
    return `${v.toFixed(1)}%`;
  if (["fiftyTwoWeekHigh", "fiftyTwoWeekLow", "targetMeanPrice"].includes(key))
    return fmtMoney2(v);
  return typeof v === "number" ? v.toFixed(2) : String(v);
}

function conviction10(s: Signal): string {
  return (s.signal_strength / 10).toFixed(1);
}

function SignalBadge({ kind, signal }: { kind: "bull" | "bear"; signal: Signal | null }) {
  if (!signal)
    return <span className="text-xs text-zinc-400">{kind === "bull" ? "🐂" : "🐻"} —</span>;
  const strong = signal.signal_strength >= 60;
  const cls = kind === "bull"
    ? strong ? "bg-emerald-600" : "bg-emerald-200 text-emerald-900 hover:bg-emerald-200"
    : strong ? "bg-red-600" : "bg-red-200 text-red-900 hover:bg-red-200";
  const label = signal.rating ?? (kind === "bull" ? "BUY" : "SELL");
  return <Badge className={cls} title={signal.rating ?? ""}>
    {kind === "bull" ? "🐂" : "🐻"} {label.length > 14 ? (kind === "bull" ? "NO BULL CASE" : "NO BEAR CASE") : label} {conviction10(signal)}
  </Badge>;
}

function JudgeBadge({ signal }: { signal: Signal | null }) {
  if (!signal) return <span className="text-xs text-zinc-400">⚖️ —</span>;
  const action = (signal.rating ?? signal.action ?? "hold").toUpperCase();
  const cls = ["BUY", "ACCUMULATE", "STRONG BUY"].includes(action) ? "bg-emerald-700"
    : ["SELL", "REDUCE", "STRONG SELL"].includes(action) ? "bg-red-700" : "bg-zinc-600";
  return (
    <Badge className={`${cls} ring-2 ring-offset-1 ring-zinc-300`}
      title={`Judge: ${action}, conviction ${conviction10(signal)}/10`}>
      ⚖️ {action} {conviction10(signal)}
    </Badge>
  );
}

function PillarBars({ ps }: { ps: PillarScores }) {
  const rows: [string, number][] = [
    ["Fundamental (40%)", ps.fundamental], ["Technical (20%)", ps.technical],
    ["Industry (20%)", ps.industry], ["Sector/Macro (20%)", ps.sector_macro],
  ];
  return (
    <div className="space-y-1">
      {rows.map(([label, v]) => (
        <div key={label} className="flex items-center gap-2 text-xs">
          <span className="w-32 shrink-0 text-zinc-500">{label}</span>
          <div className="h-2 flex-1 rounded bg-zinc-100">
            <div className={`h-2 rounded ${v >= 7 ? "bg-emerald-500" : v >= 5 ? "bg-amber-500" : "bg-red-400"}`}
              style={{ width: `${v * 10}%` }} />
          </div>
          <span className="w-8 text-right tabular-nums">{v.toFixed(1)}</span>
        </div>
      ))}
      <div className="flex justify-end text-xs font-semibold">composite {ps.composite.toFixed(1)}/10</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <details className="group">
      <summary className="cursor-pointer text-xs font-semibold text-zinc-600 hover:text-zinc-900">{title}</summary>
      <div className="mt-1 pl-1">{children}</div>
    </details>
  );
}

function CasePanel({ kind, signal }: { kind: "bull" | "bear"; signal: Signal | null }) {
  const accent = kind === "bull" ? "border-emerald-300 bg-emerald-50/50" : "border-red-300 bg-red-50/50";
  const title = kind === "bull" ? "🐂 Bull agent — long thesis" : "🐻 Bear agent — short/avoid thesis";
  if (!signal)
    return (
      <div className={`rounded-lg border p-3 ${accent}`}>
        <p className="font-semibold mb-2">{title}</p>
        <p className="text-zinc-400">No brief yet — run the debate.</p>
      </div>
    );
  const r = signal.report as CaseReport | undefined;
  return (
    <div className={`rounded-lg border p-3 space-y-2 ${accent}`}>
      <div className="flex items-baseline justify-between gap-2">
        <p className="font-semibold">{title}</p>
        <span className={`text-lg font-bold ${kind === "bull" ? "text-emerald-700" : "text-red-700"}`}>
          {conviction10(signal)}<span className="text-xs font-normal text-zinc-500">/10</span>
        </span>
      </div>
      {r?.rating && <Badge variant="outline" className="font-mono">{r.rating}</Badge>}
      <p>{signal.thesis}</p>
      {r?.pillar_scores && <PillarBars ps={r.pillar_scores} />}
      {r?.target_price_12m && (
        <p className="text-xs"><span className="font-medium">Target 12m:</span> {r.target_price_12m}</p>
      )}
      {r?.catalysts && r.catalysts.length > 0 && (
        <Section title={`Catalyst map (${r.catalysts.length})`}>
          <ul className="list-disc pl-4 text-xs space-y-0.5">{r.catalysts.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </Section>
      )}
      {r?.steelman_rebuttals && r.steelman_rebuttals.length > 0 && (
        <Section title="Steelman & rebuttals">
          <ul className="list-disc pl-4 text-xs space-y-0.5">{r.steelman_rebuttals.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </Section>
      )}
      {r?.risks && r.risks.length > 0 && (
        <Section title={`Risks to this case (${r.risks.length})`}>
          <ul className="list-disc pl-4 text-xs space-y-0.5">{r.risks.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </Section>
      )}
      {!r && signal.key_points.length > 0 && (
        <ul className="list-disc pl-4 text-xs space-y-0.5">{signal.key_points.map((k, i) => <li key={i}>{k}</li>)}</ul>
      )}
      <p className="text-xs text-zinc-500"><span className="font-medium">Invalidated if:</span> {signal.invalidation}</p>
      {r?.data_gaps && <p className="text-xs text-zinc-400">Data gaps: {r.data_gaps}</p>}
      <p className="text-xs text-zinc-400">{new Date(signal.created_at).toLocaleString()}</p>
    </div>
  );
}

function JudgePanel({ signal }: { signal: Signal }) {
  const r = signal.report as JudgeReport | undefined;
  return (
    <div className="rounded-lg border-2 border-zinc-400 bg-white p-4 space-y-3 shadow-sm">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-base font-bold">⚖️ Judge — arbitration & final recommendation</span>
        <JudgeBadge signal={signal} />
        {r?.sizing_guidance && <Badge variant="outline">{r.sizing_guidance}</Badge>}
        {r?.horizon && <span className="text-xs text-zinc-500">horizon: {r.horizon}</span>}
        <span className="ml-auto text-xs text-zinc-400">{new Date(signal.created_at).toLocaleString()}</span>
      </div>

      <p className="text-sm">{signal.thesis}</p>

      {r?.scorecard && r.scorecard.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b text-zinc-500">
                <th className="py-1 text-left font-medium">Pillar</th>
                <th className="py-1 text-right font-medium">🐂 Bull</th>
                <th className="py-1 text-right font-medium">🐻 Bear</th>
                <th className="py-1 text-center font-medium">Edge</th>
                <th className="py-1 text-left font-medium">Why</th>
              </tr>
            </thead>
            <tbody>
              {r.scorecard.map((row, i) => (
                <tr key={i} className="border-b last:border-0">
                  <td className="py-1">{row.pillar}</td>
                  <td className={`py-1 text-right tabular-nums ${row.edge === "BULL" ? "font-bold text-emerald-700" : ""}`}>{row.bull_score.toFixed(1)}</td>
                  <td className={`py-1 text-right tabular-nums ${row.edge === "BEAR" ? "font-bold text-red-700" : ""}`}>{row.bear_score.toFixed(1)}</td>
                  <td className="py-1 text-center">
                    <span className={`rounded px-1 text-[10px] font-bold ${
                      row.edge === "BULL" ? "bg-emerald-100 text-emerald-800"
                      : row.edge === "BEAR" ? "bg-red-100 text-red-800" : "bg-zinc-100 text-zinc-600"}`}>
                      {row.edge}
                    </span>
                  </td>
                  <td className="py-1 text-zinc-500">{row.why}</td>
                </tr>
              ))}
              <tr className="font-semibold">
                <td className="py-1">Weighted composite</td>
                <td className="py-1 text-right tabular-nums">{r.bull_composite.toFixed(1)}</td>
                <td className="py-1 text-right tabular-nums">{r.bear_composite.toFixed(1)}</td>
                <td className="py-1 text-center" colSpan={2}>
                  gap {(Math.abs(r.bull_composite - r.bear_composite)).toFixed(1)}
                  {Math.abs(r.bull_composite - r.bear_composite) < 1 && " → no edge territory"}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      <div className="grid md:grid-cols-2 gap-3 text-xs">
        <div className="rounded border border-emerald-200 bg-emerald-50/40 p-2">
          <p className="font-semibold text-emerald-800 mb-1">Strongest surviving Bull arguments</p>
          <ul className="list-disc pl-4 space-y-0.5">
            {(r?.strongest_bull ?? signal.key_points.slice(0, 1)).map((k, i) => <li key={i}>{k}</li>)}
          </ul>
        </div>
        <div className="rounded border border-red-200 bg-red-50/40 p-2">
          <p className="font-semibold text-red-800 mb-1">Strongest surviving Bear arguments</p>
          <ul className="list-disc pl-4 space-y-0.5">
            {(r?.strongest_bear ?? signal.key_points.slice(1, 2)).map((k, i) => <li key={i}>{k}</li>)}
          </ul>
        </div>
      </div>

      {r && (
        <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs">
          <span><span className="font-medium">Catalyst skew 0–6m:</span> {r.catalyst_skew_0_6m}</span>
          <span><span className="font-medium">6–18m:</span> {r.catalyst_skew_6_18m}</span>
          {r.reevaluation_date && <span><span className="font-medium">Re-evaluate:</span> {r.reevaluation_date}</span>}
        </div>
      )}
      {r?.material_omissions && r.material_omissions.length > 0 && (
        <p className="text-xs text-amber-700">
          <span className="font-medium">⚠ Material omissions (both agents missed):</span> {r.material_omissions.join("; ")}
        </p>
      )}
      <p className="text-xs text-zinc-500">
        <span className="font-medium">Verdict flips if:</span> {signal.invalidation}
      </p>
    </div>
  );
}

export default function MyStocks() {
  const [stocks, setStocks] = useState<Stock[]>([]);
  const [held, setHeld] = useState<Record<string, number>>({});
  const [strategyId, setStrategyId] = useState<number | null>(null);
  const [llm, setLlm] = useState(false);
  const [newSymbol, setNewSymbol] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [busyData, setBusyData] = useState(false);
  const [busyAgents, setBusyAgents] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");

  const load = useCallback(async () => {
    try {
      setStocks(await api("/api/watchlist"));
      const status = await api<{ llm_available: boolean }>("/api/agents/status");
      setLlm(status.llm_available);
      const strategies = await api<{ id: number }[]>("/api/strategies");
      setStrategyId(strategies[0]?.id ?? null);
      const portfolios = await api<{ id: number; kind: string }[]>("/api/portfolios");
      const paper = portfolios.find((p) => p.kind === "paper");
      if (paper) {
        const s = await api<{ positions: { symbol: string; qty: number }[] }>(
          `/api/portfolios/${paper.id}/summary`);
        setHeld(Object.fromEntries(s.positions.map((p) => [p.symbol, p.qty])));
      }
    } catch (e) { setError(e instanceof Error ? e.message : "load failed"); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("/api/watchlist", { method: "POST", body: JSON.stringify({ symbol: newSymbol }) });
      setNewSymbol("");
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
  }

  async function remove(symbol: string) {
    await api(`/api/watchlist/${symbol}`, { method: "DELETE" });
    load();
  }

  async function refreshData() {
    setBusyData(true); setError(""); setInfo("");
    try {
      const r = await api<{ refreshed: string[]; no_data: string[] }>("/api/watchlist/refresh", { method: "POST" });
      setInfo(`Open-source data updated for ${r.refreshed.length} stock(s)` +
        (r.no_data.length ? ` — no data found for ${r.no_data.join(", ")}` : ""));
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "refresh failed"); }
    finally { setBusyData(false); }
  }

  async function runBullBear(symbols: string[] = []) {
    setBusyAgents(true); setError(""); setInfo("");
    setInfo(symbols.length > 0
      ? `Debate running on ${symbols.join(", ")} — Bull, Bear, then Judge (≈1 min)…`
      : "Debate running on all tracked stocks (≈1-2 min)…");
    try {
      const r = await api<{ summary: string; tokens: number }>("/api/watchlist/bullbear", {
        method: "POST", body: JSON.stringify({ strategy_id: strategyId, symbols }),
      });
      setInfo(`${r.summary} (${r.tokens} tokens)`);
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "agents failed"); }
    finally { setBusyAgents(false); }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold">My Stocks</h1>
        <form onSubmit={add} className="flex gap-2 ml-auto">
          <Input value={newSymbol} onChange={(e) => setNewSymbol(e.target.value.toUpperCase())}
            placeholder="Add symbol (e.g. NVDA)" className="w-44" />
          <Button type="submit" variant="outline" disabled={!newSymbol}>Track</Button>
        </form>
        <Button onClick={refreshData} disabled={busyData || stocks.length === 0}>
          {busyData ? "Fetching…" : "⟳ Update open-source data"}
        </Button>
        <Button onClick={() => runBullBear()} disabled={busyAgents || !llm || stocks.length === 0}
          className="bg-purple-700 hover:bg-purple-800"
          title="Full debate (Bull, Bear, Judge) on every tracked stock">
          {busyAgents ? "Debating…" : "🐂🐻⚖️ Debate: all tracked"}
        </Button>
      </div>

      {!llm && (
        <p className="text-sm text-amber-700 bg-amber-50 rounded p-2">
          Bull & Bear agents need your OpenAI key — add it in Settings. Data tracking works without it.
        </p>
      )}
      {info && <p className="text-sm text-blue-700">{info}</p>}
      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="space-y-3">
        {stocks.map((s) => {
          const isOpen = expanded === s.symbol;
          const stale = !s.data_as_of;
          return (
            <Card key={s.symbol} className={isOpen ? "border-blue-400" : ""}>
              <CardContent className="pt-4">
                <div className="flex flex-wrap items-center gap-3">
                  <button onClick={() => setExpanded(isOpen ? null : s.symbol)}
                    className="font-bold text-lg hover:text-blue-700">{s.symbol}</button>
                  <span className="text-sm text-zinc-500 truncate max-w-48">
                    {String(s.fundamentals.longName ?? "")}
                  </span>
                  {held[s.symbol] && <Badge variant="outline">holding {held[s.symbol]}</Badge>}
                  <span className="font-medium">{s.price != null ? fmtMoney2(s.price) : "no data yet"}</span>
                  <span className={`text-xs ${stale ? "text-amber-600" : "text-zinc-400"}`}>
                    {s.data_as_of ? `data: ${new Date(s.data_as_of).toLocaleString()}` : "press “Update open-source data”"}
                  </span>
                  <div className="ml-auto flex items-center gap-2">
                    <JudgeBadge signal={s.judge} />
                    <SignalBadge kind="bull" signal={s.bull} />
                    <SignalBadge kind="bear" signal={s.bear} />
                    <button onClick={() => runBullBear([s.symbol])} disabled={busyAgents || !llm}
                      title={`Run the full debate for ${s.symbol} only`}
                      className="rounded border border-purple-300 px-1.5 py-0.5 text-xs text-purple-700 hover:bg-purple-50 disabled:opacity-40">
                      🐂🐻⚖️ run
                    </button>
                    <button onClick={() => setExpanded(isOpen ? null : s.symbol)}
                      className="text-sm text-blue-600 hover:underline">{isOpen ? "Close" : "Details"}</button>
                    <button onClick={() => remove(s.symbol)} title="Stop tracking"
                      className="text-zinc-400 hover:text-red-600">✕</button>
                  </div>
                </div>

                {isOpen && s.judge && (
                  <div className="mt-4">
                    <JudgePanel signal={s.judge} />
                  </div>
                )}
                {isOpen && (
                  <div className="mt-4 grid md:grid-cols-3 gap-4 text-sm">
                    <div className="rounded-lg border p-3">
                      <p className="font-semibold mb-2">Open-source data</p>
                      <table className="w-full">
                        <tbody>
                          {IND_LABELS.filter(([k]) => s.indicators[k] !== undefined).map(([k, label]) => (
                            <tr key={k} className="border-b last:border-0">
                              <td className="py-1 text-zinc-500">{label}</td>
                              <td className="py-1 text-right font-medium">{fmtVal(k, s.indicators[k])}</td>
                            </tr>
                          ))}
                          {FUND_LABELS.filter(([k]) => s.fundamentals[k] !== undefined && k !== "longName").map(([k, label]) => (
                            <tr key={k} className="border-b last:border-0">
                              <td className="py-1 text-zinc-500">{label}</td>
                              <td className="py-1 text-right font-medium">{fmtVal(k, s.fundamentals[k])}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {Object.keys(s.indicators).length === 0 && Object.keys(s.fundamentals).length === 0 && (
                        <p className="text-zinc-400">No data fetched yet.</p>
                      )}
                    </div>

                    <CasePanel kind="bull" signal={s.bull} />
                    <CasePanel kind="bear" signal={s.bear} />
                  </div>
                )}
              </CardContent>
            </Card>
          );
        })}
        {stocks.length === 0 && (
          <Card><CardContent className="pt-4 text-sm text-zinc-500">
            You&apos;re not tracking any stock yet. Add a symbol above — then “Update open-source data”
            fetches prices, technicals and fundamentals, and the Bull, Bear &amp; Judge agents debate each one.
          </CardContent></Card>
        )}
      </div>
    </div>
  );
}
