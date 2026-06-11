"use client";

import { useCallback, useEffect, useState } from "react";
import { api, fmtMoney2 } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

type Signal = {
  signal_strength: number; thesis: string; key_points: string[];
  invalidation: string; created_at: string;
};
type Stock = {
  symbol: string; added_at: string; price: number | null; data_as_of: string | null;
  indicators: Record<string, number | boolean>;
  fundamentals: Record<string, string | number>;
  bull: Signal | null; bear: Signal | null;
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

function SignalBadge({ kind, signal }: { kind: "bull" | "bear"; signal: Signal | null }) {
  if (!signal)
    return <span className="text-xs text-zinc-400">{kind === "bull" ? "🐂" : "🐻"} —</span>;
  const s = signal.signal_strength;
  const strong = s >= 60;
  const cls = kind === "bull"
    ? strong ? "bg-emerald-600" : "bg-emerald-200 text-emerald-900 hover:bg-emerald-200"
    : strong ? "bg-red-600" : "bg-red-200 text-red-900 hover:bg-red-200";
  return <Badge className={cls}>{kind === "bull" ? "🐂 Buy" : "🐻 Sell"} {s.toFixed(0)}</Badge>;
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
      const portfolios = await api<{ id: number }[]>("/api/portfolios");
      if (portfolios.length > 0) {
        const s = await api<{ positions: { symbol: string; qty: number }[] }>(
          `/api/portfolios/${portfolios[0].id}/summary`);
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

  async function runBullBear() {
    if (!strategyId) { setError("Create a strategy first (Setup → My Strategy)"); return; }
    setBusyAgents(true); setError(""); setInfo("");
    try {
      const r = await api<{ summary: string; tokens: number }>("/api/watchlist/bullbear", {
        method: "POST", body: JSON.stringify({ strategy_id: strategyId }),
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
        <Button onClick={runBullBear} disabled={busyAgents || !llm || stocks.length === 0}
          className="bg-purple-700 hover:bg-purple-800">
          {busyAgents ? "Debating… (≈30s)" : "🐂🐻 Run Bull & Bear agents"}
        </Button>
      </div>

      {!llm && (
        <p className="text-sm text-amber-700 bg-amber-50 rounded p-2">
          Bull &amp; Bear agents need OPENAI_API_KEY on the backend — data tracking works without it.
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
                    <SignalBadge kind="bull" signal={s.bull} />
                    <SignalBadge kind="bear" signal={s.bear} />
                    <button onClick={() => setExpanded(isOpen ? null : s.symbol)}
                      className="text-sm text-blue-600 hover:underline">{isOpen ? "Close" : "Details"}</button>
                    <button onClick={() => remove(s.symbol)} title="Stop tracking"
                      className="text-zinc-400 hover:text-red-600">✕</button>
                  </div>
                </div>

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

                    <SignalPanel kind="bull" title="🐂 Bull agent — case to BUY" signal={s.bull} />
                    <SignalPanel kind="bear" title="🐻 Bear agent — case to SELL" signal={s.bear} />
                  </div>
                )}
              </CardContent>
            </Card>
          );
        })}
        {stocks.length === 0 && (
          <Card><CardContent className="pt-4 text-sm text-zinc-500">
            You&apos;re not tracking any stock yet. Add a symbol above — then “Update open-source data”
            fetches prices, technicals and fundamentals, and the Bull &amp; Bear agents argue both sides.
          </CardContent></Card>
        )}
      </div>
    </div>
  );
}

function SignalPanel({ kind, title, signal }: { kind: "bull" | "bear"; title: string; signal: Signal | null }) {
  const accent = kind === "bull" ? "border-emerald-300 bg-emerald-50/50" : "border-red-300 bg-red-50/50";
  if (!signal)
    return (
      <div className={`rounded-lg border p-3 ${accent}`}>
        <p className="font-semibold mb-2">{title}</p>
        <p className="text-zinc-400">No signal yet — run the Bull &amp; Bear agents.</p>
      </div>
    );
  return (
    <div className={`rounded-lg border p-3 ${accent}`}>
      <p className="font-semibold mb-1">{title}</p>
      <p className={`text-2xl font-bold ${kind === "bull" ? "text-emerald-700" : "text-red-700"}`}>
        {signal.signal_strength.toFixed(0)}<span className="text-sm font-normal text-zinc-500">/100</span>
      </p>
      <p className="mt-1">{signal.thesis}</p>
      {signal.key_points.length > 0 && (
        <ul className="mt-2 list-disc pl-4 text-zinc-700 space-y-0.5">
          {signal.key_points.map((k, i) => <li key={i}>{k}</li>)}
        </ul>
      )}
      <p className="mt-2 text-xs text-zinc-500">
        <span className="font-medium">Invalidated if:</span> {signal.invalidation}
      </p>
      <p className="mt-1 text-xs text-zinc-400">{new Date(signal.created_at).toLocaleString()}</p>
    </div>
  );
}
