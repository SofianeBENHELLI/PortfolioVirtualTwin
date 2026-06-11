"use client";

import { useCallback, useEffect, useState } from "react";
import { API_URL, api, fmtMoney, getToken } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import EquityChart from "@/components/equity-chart";

type Run = {
  id: number; status: string; params: { symbols: string[]; start: string; end: string };
  metrics: Record<string, number>; equity_curve: { dates: string[]; strategy: number[]; benchmark: number[] };
  skipped_rules: { rule: string; kind: string; reason: string }[];
  error: string; has_tearsheet: boolean; created_at: string;
};

export default function BacktestLab() {
  const [strategies, setStrategies] = useState<{ id: number; name: string }[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState<Run | null>(null);
  const [error, setError] = useState("");

  const [strategyId, setStrategyId] = useState("");
  const [start, setStart] = useState("2023-01-01");
  const [end, setEnd] = useState(new Date().toISOString().slice(0, 10));
  const [cash, setCash] = useState("100000");
  const [tearsheet, setTearsheet] = useState(true);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setStrategies(await api("/api/strategies"));
      const rs = await api<Run[]>("/api/backtests");
      setRuns(rs);
      setSelected((cur) => (cur ? rs.find((r) => r.id === cur.id) ?? rs[0] ?? null : rs[0] ?? null));
    } catch (e) { setError(e instanceof Error ? e.message : "load failed"); }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 8_000);
    return () => clearInterval(t);
  }, [load]);

  async function launch(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await api("/api/backtests", {
        method: "POST",
        body: JSON.stringify({
          strategy_id: parseInt(strategyId), symbols: [], start, end,
          initial_cash: parseFloat(cash), with_tearsheet: tearsheet,
        }),
      });
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
    finally { setBusy(false); }
  }

  const m = selected?.metrics ?? {};

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Backtest Lab</h1>

      <Card>
        <CardContent className="pt-4">
          <form onSubmit={launch} className="flex flex-wrap items-end gap-3">
            <label className="text-sm">Strategy
              <select value={strategyId} onChange={(e) => setStrategyId(e.target.value)} required
                className="mt-1 block border rounded-md h-9 px-2 text-sm min-w-48">
                <option value="">— pick —</option>
                {strategies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </label>
            <label className="text-sm">Start
              <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} className="mt-1" /></label>
            <label className="text-sm">End
              <Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className="mt-1" /></label>
            <label className="text-sm">Initial cash
              <Input type="number" value={cash} onChange={(e) => setCash(e.target.value)} className="mt-1 w-32" /></label>
            <label className="text-sm flex items-center gap-2 pb-2">
              <input type="checkbox" checked={tearsheet} onChange={(e) => setTearsheet(e.target.checked)} />
              QuantStats tearsheet
            </label>
            <Button type="submit" disabled={busy}>Run backtest</Button>
          </form>
          <p className="mt-2 text-xs text-zinc-500">
            Uses the strategy&apos;s universe symbols. Rules based on fundamental/sentiment scores are skipped
            (listed transparently below) — they are evaluated by the research agent at proposal time instead.
          </p>
          {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
        </CardContent>
      </Card>

      <div className="grid lg:grid-cols-4 gap-6">
        <Card>
          <CardHeader><CardTitle className="text-base">Runs</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            {runs.map((r) => (
              <button key={r.id} onClick={() => setSelected(r)}
                className={`w-full text-left border rounded-md p-2 text-sm hover:border-blue-400 ${
                  selected?.id === r.id ? "border-blue-500 bg-blue-50" : ""}`}>
                <div className="flex items-center gap-2">
                  <Badge className={r.status === "done" ? "bg-emerald-600" : r.status === "failed" ? "bg-red-600" : "bg-amber-500"}>
                    {r.status}
                  </Badge>
                  <span>#{r.id}</span>
                </div>
                <div className="text-xs text-zinc-500">{r.params.start} → {r.params.end}</div>
              </button>
            ))}
            {runs.length === 0 && <p className="text-sm text-zinc-400">No runs yet</p>}
          </CardContent>
        </Card>

        <div className="lg:col-span-3 space-y-6">
          {selected && selected.status === "done" && (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <Metric label="Strategy return" v={`${m.total_return_pct?.toFixed(1)}%`} />
                <Metric label="Benchmark return" v={`${m.benchmark_return_pct?.toFixed(1)}%`} />
                <Metric label="Max drawdown" v={`${m.max_drawdown_pct?.toFixed(1)}%`} />
                <Metric label="Sharpe" v={m.sharpe?.toFixed(2)} />
                <Metric label="Trades" v={String(m.n_trades)} />
                <Metric label="Win rate" v={`${m.win_rate_pct?.toFixed(0)}%`} />
                <Metric label="Volatility" v={`${m.volatility_pct?.toFixed(1)}%`} />
                <Metric label="Final equity" v={fmtMoney(m.final_equity)} />
              </div>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between">
                  <CardTitle className="text-base">Equity vs benchmark</CardTitle>
                  {selected.has_tearsheet && (
                    <a className="text-sm text-blue-600 hover:underline"
                      href={`${API_URL}/api/backtests/${selected.id}/tearsheet`}
                      onClick={async (e) => {
                        e.preventDefault();
                        const res = await fetch(`${API_URL}/api/backtests/${selected.id}/tearsheet`,
                          { headers: { Authorization: `Bearer ${getToken()}` } });
                        const blob = await res.blob();
                        window.open(URL.createObjectURL(blob), "_blank");
                      }}>
                      Open QuantStats tearsheet ↗
                    </a>
                  )}
                </CardHeader>
                <CardContent>
                  <EquityChart dates={selected.equity_curve.dates} series={[
                    { name: "Strategy", values: selected.equity_curve.strategy, color: "#2563eb" },
                    { name: "Benchmark", values: selected.equity_curve.benchmark, color: "#a1a1aa" },
                  ]} />
                </CardContent>
              </Card>
              {selected.skipped_rules.length > 0 && (
                <Card className="border-amber-300">
                  <CardHeader><CardTitle className="text-base">Rules not included in this backtest</CardTitle></CardHeader>
                  <CardContent>
                    <ul className="text-sm space-y-1">
                      {selected.skipped_rules.map((s, i) => (
                        <li key={i}><span className="font-mono text-xs">{s.rule}</span>
                          <span className="text-zinc-500"> — {s.reason}</span></li>
                      ))}
                    </ul>
                  </CardContent>
                </Card>
              )}
            </>
          )}
          {selected?.status === "failed" && (
            <Card className="border-red-300"><CardContent className="pt-4 text-sm text-red-700">
              Backtest failed: {selected.error}
            </CardContent></Card>
          )}
          {selected?.status === "running" && <p className="text-zinc-500">Running… (auto-refreshes)</p>}
        </div>
      </div>
    </div>
  );
}

function Metric({ label, v }: { label: string; v?: string }) {
  return (
    <Card><CardContent className="pt-4">
      <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="text-lg font-semibold">{v ?? "—"}</div>
    </CardContent></Card>
  );
}
