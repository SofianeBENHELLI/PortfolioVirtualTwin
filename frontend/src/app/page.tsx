"use client";

import { useCallback, useEffect, useState } from "react";
import { api, fmtMoney, fmtMoney2, fmtPct, pnlColor } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import EquityChart from "@/components/equity-chart";

type Position = {
  symbol: string; qty: number; avg_entry_price: number; price: number; value: number;
  unrealized_pnl: number; unrealized_pnl_pct: number; realized_pnl: number;
};
type Summary = {
  portfolio_id: number; name: string; mode: string; broker: string; cash: number; equity: number;
  initial_cash: number; total_pnl: number; total_pnl_pct: number; daily_pnl: number; daily_pnl_pct: number;
  unrealized_pnl: number; realized_pnl: number; drawdown_pct: number; max_drawdown_pct: number;
  volatility_pct: number; n_positions: number; top_position_weight_pct: number;
  sector_weights: Record<string, number>; open_orders: number;
  best_position: string | null; worst_position: string | null; positions: Position[];
};
type Report = { id: number; narrative: string; created_at: string };

function Kpi({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <Card>
      <CardContent className="pt-4">
        <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
        <div className={`text-xl font-semibold ${color ?? ""}`}>{value}</div>
        {sub && <div className="text-xs text-zinc-500">{sub}</div>}
      </CardContent>
    </Card>
  );
}

export default function Dashboard() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [equity, setEquity] = useState<{ dates: string[]; equity: number[] }>({ dates: [], equity: [] });
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const [briefBusy, setBriefBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const portfolios = await api<{ id: number }[]>("/api/portfolios");
      if (portfolios.length === 0) {
        setError("No portfolio yet — create one from the Trading Console.");
        return;
      }
      const id = portfolios[0].id;
      setSummary(await api<Summary>(`/api/portfolios/${id}/summary`));
      setEquity(await api(`/api/portfolios/${id}/equity`));
      const reports = await api<Report[]>("/api/agents/reports");
      setReport(reports[0] ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "load failed");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  async function briefing() {
    if (!summary) return;
    setBriefBusy(true);
    try {
      const r = await api<Report>("/api/agents/explain", {
        method: "POST",
        body: JSON.stringify({ portfolio_id: summary.portfolio_id }),
      });
      setReport(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "briefing failed");
    } finally {
      setBriefBusy(false);
    }
  }

  if (!summary)
    return <div className="text-zinc-500">{error || "Loading portfolio…"}</div>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Portfolio Dashboard</h1>
        <Button onClick={briefing} disabled={briefBusy} variant="outline">
          {briefBusy ? "Generating…" : "AI daily briefing"}
        </Button>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
        <Kpi label="Equity" value={fmtMoney(summary.equity)} sub={`cash ${fmtMoney(summary.cash)}`} />
        <Kpi label="Total P&L" value={fmtMoney(summary.total_pnl)} sub={fmtPct(summary.total_pnl_pct)}
          color={pnlColor(summary.total_pnl)} />
        <Kpi label="Daily P&L" value={fmtMoney(summary.daily_pnl)} sub={fmtPct(summary.daily_pnl_pct)}
          color={pnlColor(summary.daily_pnl)} />
        <Kpi label="Unrealized" value={fmtMoney(summary.unrealized_pnl)} color={pnlColor(summary.unrealized_pnl)} />
        <Kpi label="Realized" value={fmtMoney(summary.realized_pnl)} color={pnlColor(summary.realized_pnl)} />
        <Kpi label="Drawdown" value={`${summary.drawdown_pct.toFixed(1)}%`}
          sub={`max ${summary.max_drawdown_pct.toFixed(1)}%`} />
      </div>

      <Card>
        <CardHeader><CardTitle className="text-base">Equity curve</CardTitle></CardHeader>
        <CardContent>
          <EquityChart dates={equity.dates}
            series={[{ name: "Equity", values: equity.equity, color: "#2563eb" }]} />
        </CardContent>
      </Card>

      <div className="grid lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle className="text-base">Positions ({summary.n_positions})</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Symbol</TableHead><TableHead>Qty</TableHead><TableHead>Entry</TableHead>
                  <TableHead>Price</TableHead><TableHead>Value</TableHead><TableHead>Unrealized</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {summary.positions.map((p) => (
                  <TableRow key={p.symbol}>
                    <TableCell className="font-medium">{p.symbol}
                      {p.symbol === summary.best_position && <Badge className="ml-2 bg-emerald-600">best</Badge>}
                      {p.symbol === summary.worst_position && <Badge className="ml-2 bg-red-600">worst</Badge>}
                    </TableCell>
                    <TableCell>{p.qty}</TableCell>
                    <TableCell>{fmtMoney2(p.avg_entry_price)}</TableCell>
                    <TableCell>{fmtMoney2(p.price)}</TableCell>
                    <TableCell>{fmtMoney(p.value)}</TableCell>
                    <TableCell className={pnlColor(p.unrealized_pnl)}>
                      {fmtMoney(p.unrealized_pnl)} ({fmtPct(p.unrealized_pnl_pct, 1)})
                    </TableCell>
                  </TableRow>
                ))}
                {summary.positions.length === 0 && (
                  <TableRow><TableCell colSpan={6} className="text-zinc-400">No positions</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-base">Latest briefing</CardTitle></CardHeader>
          <CardContent>
            {report ? (
              <div className="text-sm whitespace-pre-wrap">{report.narrative}</div>
            ) : (
              <p className="text-sm text-zinc-400">
                No briefing yet. Click “AI daily briefing” (requires OpenAI key on the backend).
              </p>
            )}
          </CardContent>
        </Card>
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
    </div>
  );
}
