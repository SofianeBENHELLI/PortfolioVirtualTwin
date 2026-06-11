"use client";

import { useCallback, useEffect, useState } from "react";
import { api, fmtMoney, fmtMoney2, fmtPct, pnlColor } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import EquityChart from "@/components/equity-chart";

type Position = {
  symbol: string; qty: number; avg_entry_price: number; price: number; value: number;
  unrealized_pnl: number; unrealized_pnl_pct: number; realized_pnl: number;
};
type Summary = {
  portfolio_id: number; name: string; mode: string; kind: string; broker: string; cash: number;
  equity: number; initial_cash: number; cost_basis: number; total_pnl: number; total_pnl_pct: number;
  daily_pnl: number; daily_pnl_pct: number; unrealized_pnl: number; realized_pnl: number;
  drawdown_pct: number; max_drawdown_pct: number; n_positions: number;
  best_position: string | null; worst_position: string | null; positions: Position[];
};
type PortfolioRow = {
  id: number; kind: string; name: string; live_armed?: boolean;
  max_order_notional?: number; max_live_orders_per_day?: number;
};
type Report = { id: number; narrative: string; created_at: string };
type Readiness = { armed: boolean; all_passed: boolean;
  checks: { name: string; passed: boolean; detail: string }[] };
type MacroStrip = { indicators: Record<string, { label: string; value: number; chg_1d_pct: number }>;
  regimes: Record<string, unknown> };
type Signal = { signal_strength: number; created_at: string };
type SignalMap = Record<string, { bull?: Signal; bear?: Signal }>;

function Kpi({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="rounded-lg border bg-white p-3">
      <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
      <div className={`text-lg font-semibold ${color ?? ""}`}>{value}</div>
      {sub && <div className="text-xs text-zinc-500">{sub}</div>}
    </div>
  );
}

function SignalChips({ s }: { s?: { bull?: Signal; bear?: Signal } }) {
  if (!s || (!s.bull && !s.bear)) return <span className="text-xs text-zinc-300">—</span>;
  return (
    <span className="inline-flex gap-1">
      {s.bull && (
        <span className={`rounded px-1 text-xs font-medium ${s.bull.signal_strength >= 60 ? "bg-emerald-600 text-white" : "bg-emerald-100 text-emerald-800"}`}>
          🐂{s.bull.signal_strength.toFixed(0)}</span>
      )}
      {s.bear && (
        <span className={`rounded px-1 text-xs font-medium ${s.bear.signal_strength >= 60 ? "bg-red-600 text-white" : "bg-red-100 text-red-800"}`}>
          🐻{s.bear.signal_strength.toFixed(0)}</span>
      )}
    </span>
  );
}

function PositionsTable({ positions, best, worst, signals }: {
  positions: Position[]; best: string | null; worst: string | null; signals: SignalMap;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Symbol</TableHead><TableHead>Qty</TableHead><TableHead>Entry</TableHead>
          <TableHead>Price</TableHead><TableHead>Value</TableHead><TableHead>Unrealized</TableHead>
          <TableHead title="latest Bull / Bear agent signals">🐂🐻</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {positions.map((p) => (
          <TableRow key={p.symbol}>
            <TableCell className="font-medium">{p.symbol}
              {p.symbol === best && <Badge className="ml-2 bg-emerald-600">best</Badge>}
              {p.symbol === worst && <Badge className="ml-2 bg-red-600">worst</Badge>}
            </TableCell>
            <TableCell>{p.qty}</TableCell>
            <TableCell>{fmtMoney2(p.avg_entry_price)}</TableCell>
            <TableCell>{fmtMoney2(p.price)}</TableCell>
            <TableCell>{fmtMoney(p.value)}</TableCell>
            <TableCell className={pnlColor(p.unrealized_pnl)}>
              {fmtMoney(p.unrealized_pnl)} ({fmtPct(p.unrealized_pnl_pct, 1)})
            </TableCell>
            <TableCell><SignalChips s={signals[p.symbol]} /></TableCell>
          </TableRow>
        ))}
        {positions.length === 0 && (
          <TableRow><TableCell colSpan={7} className="text-zinc-400">No positions</TableCell></TableRow>
        )}
      </TableBody>
    </Table>
  );
}

export default function Dashboard() {
  const [paper, setPaper] = useState<Summary | null>(null);
  const [real, setReal] = useState<Summary | null>(null);
  const [realRow, setRealRow] = useState<PortfolioRow | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [macro, setMacro] = useState<MacroStrip | null>(null);
  const [signals, setSignals] = useState<SignalMap>({});
  const [hasReal, setHasReal] = useState(true);
  const [armBusy, setArmBusy] = useState(false);
  const [bbBusy, setBbBusy] = useState<number | null>(null);
  const [llm, setLlm] = useState(false);
  const [info, setInfo] = useState("");
  const [paperEquity, setPaperEquity] = useState<{ dates: string[]; equity: number[] }>({ dates: [], equity: [] });
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const [briefBusy, setBriefBusy] = useState(false);

  // add-holding form (real portfolio)
  const [hSymbol, setHSymbol] = useState("");
  const [hQty, setHQty] = useState("");
  const [hPrice, setHPrice] = useState("");

  const load = useCallback(async () => {
    try {
      const ps = await api<PortfolioRow[]>("/api/portfolios");
      const paperP = ps.find((p) => p.kind === "paper");
      const realP = ps.find((p) => p.kind === "real_tracked");
      setHasReal(!!realP);
      if (paperP) {
        setPaper(await api<Summary>(`/api/portfolios/${paperP.id}/summary`));
        setPaperEquity(await api(`/api/portfolios/${paperP.id}/equity`));
      }
      if (realP) {
        setRealRow(realP);
        setReal(await api<Summary>(`/api/portfolios/${realP.id}/summary`));
        setReadiness(await api<Readiness>(`/api/portfolios/${realP.id}/readiness`));
      }
      const reports = await api<Report[]>("/api/agents/reports");
      setReport(reports[0] ?? null);
      try {
        const m = await api<{ snapshot: MacroStrip | null }>("/api/macro");
        setMacro(m.snapshot);
        const status = await api<{ llm_available: boolean }>("/api/agents/status");
        setLlm(status.llm_available);
      } catch {}
    } catch (e) { setError(e instanceof Error ? e.message : "load failed"); }
  }, []);

  // latest bull/bear signals for every held symbol (paper + real)
  useEffect(() => {
    const syms = [...new Set([...(paper?.positions ?? []), ...(real?.positions ?? [])]
      .map((p) => p.symbol))];
    if (syms.length === 0) return;
    api<SignalMap>(`/api/watchlist/signals?symbols=${syms.join(",")}`)
      .then(setSignals).catch(() => {});
  }, [paper, real]);

  async function bullBearHoldings(portfolioId: number) {
    setBbBusy(portfolioId); setError(""); setInfo("");
    try {
      const r = await api<{ summary: string }>("/api/watchlist/bullbear", {
        method: "POST", body: JSON.stringify({ portfolio_id: portfolioId }),
      });
      setInfo(r.summary);
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "agents failed"); }
    finally { setBbBusy(null); }
  }

  async function toggleArm() {
    if (!realRow) return;
    setArmBusy(true); setError("");
    try {
      await api(`/api/portfolios/${realRow.id}/${realRow.live_armed ? "disarm" : "arm"}`, { method: "POST" });
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "arming failed"); }
    finally { setArmBusy(false); }
  }

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  async function createReal() {
    setError("");
    try {
      await api("/api/portfolios", { method: "POST", body: JSON.stringify({ kind: "real_tracked" }) });
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
  }

  async function addHolding(e: React.FormEvent) {
    e.preventDefault();
    if (!real) return;
    setError("");
    try {
      await api(`/api/portfolios/${real.portfolio_id}/holdings`, {
        method: "POST",
        body: JSON.stringify({ symbol: hSymbol, qty: parseFloat(hQty), avg_entry_price: parseFloat(hPrice) }),
      });
      setHSymbol(""); setHQty(""); setHPrice("");
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
  }

  async function removeHolding(symbol: string) {
    if (!real) return;
    await api(`/api/portfolios/${real.portfolio_id}/holdings/${symbol}`, { method: "DELETE" });
    load();
  }

  async function briefing() {
    if (!paper) return;
    setBriefBusy(true);
    try {
      const r = await api<Report>("/api/agents/explain", {
        method: "POST", body: JSON.stringify({ portfolio_id: paper.portfolio_id }),
      });
      setReport(r);
    } catch (e) { setError(e instanceof Error ? e.message : "briefing failed"); }
    finally { setBriefBusy(false); }
  }

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Portfolio</h1>
        <Button onClick={briefing} disabled={briefBusy || !paper} variant="outline">
          {briefBusy ? "Generating…" : "AI daily briefing"}
        </Button>
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      {info && <p className="text-sm text-blue-700">{info}</p>}

      {macro && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border bg-white px-4 py-2 text-sm">
          <span className="text-xs uppercase tracking-wide text-zinc-400">Macro</span>
          {["vix", "wti", "gold", "us10y"].map((k) => {
            const ind = macro.indicators[k];
            if (!ind) return null;
            return (
              <span key={k} className="tabular-nums">
                {ind.label.split(" ")[0]} <b>{ind.value >= 100 ? ind.value.toFixed(0) : ind.value.toFixed(2)}</b>
                <span className={ind.chg_1d_pct >= 0 ? "text-emerald-600" : "text-red-600"}>
                  {" "}{ind.chg_1d_pct >= 0 ? "+" : ""}{ind.chg_1d_pct.toFixed(1)}%
                </span>
              </span>
            );
          })}
          {macro.regimes.risk_off === true && <Badge className="bg-red-600">risk-off</Badge>}
          {macro.regimes.volatility_regime === "high" && <Badge className="bg-red-600">high vol</Badge>}
          {macro.regimes.volatility_regime === "elevated" && <Badge className="bg-amber-500">elevated vol</Badge>}
          {macro.regimes.war_risk === "high" && <Badge className="bg-red-600">war risk</Badge>}
          {macro.regimes.oil_shock === true && <Badge className="bg-amber-500">oil shock</Badge>}
          {macro.regimes.gold_rush === true && <Badge className="bg-yellow-500 text-black">gold rush</Badge>}
          <a href="/macro" className="ml-auto text-xs text-blue-600 hover:underline">Macro & World →</a>
        </div>
      )}

      {/* ------------------------------------------------ PAPER ------------- */}
      <section className="rounded-xl border-2 border-amber-300 bg-amber-50/40 p-4 space-y-4">
        <div className="flex items-center gap-3 flex-wrap">
          <Badge className="bg-amber-500 text-black hover:bg-amber-500">PAPER — SIMULATED</Badge>
          <span className="font-semibold">{paper?.name ?? "Paper portfolio"}</span>
          {paper && <span className="text-sm text-zinc-500">broker: {paper.broker}</span>}
          {paper && paper.n_positions > 0 && (
            <Button size="sm" variant="outline" className="border-purple-300 text-purple-700"
              onClick={() => bullBearHoldings(paper.portfolio_id)}
              disabled={bbBusy !== null || !llm}
              title="Run the Bull & Bear agents on every paper holding">
              {bbBusy === paper.portfolio_id ? "Debating…" : "🐂🐻 Bull & Bear my holdings"}
            </Button>
          )}
          <span className="ml-auto text-xs text-zinc-500">Traded by you via the approval pipeline. Not real money.</span>
        </div>
        {paper ? (
          <>
            <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
              <Kpi label="Equity" value={fmtMoney(paper.equity)} sub={`cash ${fmtMoney(paper.cash)}`} />
              <Kpi label="Total P&L" value={fmtMoney(paper.total_pnl)} sub={fmtPct(paper.total_pnl_pct)} color={pnlColor(paper.total_pnl)} />
              <Kpi label="Daily P&L" value={fmtMoney(paper.daily_pnl)} sub={fmtPct(paper.daily_pnl_pct)} color={pnlColor(paper.daily_pnl)} />
              <Kpi label="Unrealized" value={fmtMoney(paper.unrealized_pnl)} color={pnlColor(paper.unrealized_pnl)} />
              <Kpi label="Realized" value={fmtMoney(paper.realized_pnl)} color={pnlColor(paper.realized_pnl)} />
              <Kpi label="Drawdown" value={`${paper.drawdown_pct.toFixed(1)}%`} sub={`max ${paper.max_drawdown_pct.toFixed(1)}%`} />
            </div>
            <div className="grid lg:grid-cols-2 gap-4">
              <Card>
                <CardHeader><CardTitle className="text-base">Equity curve</CardTitle></CardHeader>
                <CardContent>
                  <EquityChart dates={paperEquity.dates} height={220}
                    series={[{ name: "Equity", values: paperEquity.equity, color: "#d97706" }]} />
                </CardContent>
              </Card>
              <Card>
                <CardHeader><CardTitle className="text-base">Paper positions ({paper.n_positions})</CardTitle></CardHeader>
                <CardContent>
                  <PositionsTable positions={paper.positions} best={paper.best_position}
                    worst={paper.worst_position} signals={signals} />
                </CardContent>
              </Card>
            </div>
          </>
        ) : (
          <p className="text-sm text-zinc-500">No paper portfolio yet — create one in the Trading Console.</p>
        )}
      </section>

      {/* ------------------------------------------------ REAL -------------- */}
      <section className="rounded-xl border-2 border-emerald-300 bg-emerald-50/40 p-4 space-y-4">
        <div className="flex items-center gap-3 flex-wrap">
          <Badge className="bg-emerald-600 hover:bg-emerald-600">REAL — TRACKED ONLY</Badge>
          <span className="font-semibold">{real?.name ?? "Your real portfolio"}</span>
          {real && real.n_positions > 0 && (
            <Button size="sm" variant="outline" className="border-purple-300 text-purple-700"
              onClick={() => bullBearHoldings(real.portfolio_id)}
              disabled={bbBusy !== null || !llm}
              title="Run the Bull & Bear agents on every real holding">
              {bbBusy === real.portfolio_id ? "Debating…" : "🐂🐻 Bull & Bear my holdings"}
            </Button>
          )}
          <span className="ml-auto text-xs text-zinc-500">
            Mirror of your actual brokerage account. The app manages orders; it never sends them to a broker API.
          </span>
        </div>
        {!hasReal ? (
          <div className="text-sm text-zinc-600 flex items-center gap-4">
            <p>Track your real holdings here to compare them with your paper strategy and include them in risk views.</p>
            <Button onClick={createReal} variant="outline" className="border-emerald-500">Start tracking my real portfolio</Button>
          </div>
        ) : real && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <Kpi label="Market value" value={fmtMoney(real.equity)} />
              <Kpi label="Cost basis" value={fmtMoney(real.cost_basis)} />
              <Kpi label="Total P&L" value={fmtMoney(real.total_pnl)} sub={fmtPct(real.total_pnl_pct)} color={pnlColor(real.total_pnl)} />
              <Kpi label="Daily P&L" value={fmtMoney(real.daily_pnl)} sub={fmtPct(real.daily_pnl_pct)} color={pnlColor(real.daily_pnl)} />
              <Kpi label="Holdings" value={String(real.n_positions)} />
            </div>

            <Card className={realRow?.live_armed ? "border-red-400" : ""}>
              <CardHeader className="flex flex-row items-center justify-between">
                <CardTitle className="text-base">
                  Real trading {realRow?.live_armed
                    ? <Badge className="ml-2 bg-red-600">ARMED</Badge>
                    : <Badge variant="outline" className="ml-2">disarmed</Badge>}
                </CardTitle>
                <Button onClick={toggleArm} disabled={armBusy || (!realRow?.live_armed && !readiness?.all_passed)}
                  variant={realRow?.live_armed ? "outline" : "default"}
                  className={realRow?.live_armed ? "" : "bg-red-600 hover:bg-red-700"}>
                  {armBusy ? "…" : realRow?.live_armed ? "Disarm" : "Arm real trading"}
                </Button>
              </CardHeader>
              <CardContent className="space-y-2">
                <p className="text-xs text-zinc-500">
                  Arming lets order proposals through the risk gateway for this portfolio. The app still
                  never calls a broker API: approved real orders wait for YOU to execute them at your
                  broker and record the fill. Caps: {fmtMoney(realRow?.max_order_notional ?? 0)}/order,
                  {" "}{realRow?.max_live_orders_per_day} orders/day (adjust in Trading Console via API).
                </p>
                <ul className="grid md:grid-cols-2 gap-x-6 gap-y-1 text-sm">
                  {(readiness?.checks ?? []).map((c) => (
                    <li key={c.name} className="flex gap-2">
                      <span className={c.passed ? "text-emerald-600" : "text-red-600"}>{c.passed ? "✓" : "✗"}</span>
                      <span className="font-mono text-xs pt-0.5">{c.name}</span>
                      <span className="text-zinc-500 text-xs pt-0.5">{c.detail}</span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle className="text-base">Real holdings (entered by you)</CardTitle></CardHeader>
              <CardContent className="space-y-3">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Symbol</TableHead><TableHead>Qty</TableHead><TableHead>Avg entry</TableHead>
                      <TableHead>Price</TableHead><TableHead>Value</TableHead><TableHead>Unrealized</TableHead>
                      <TableHead title="latest Bull / Bear agent signals">🐂🐻</TableHead><TableHead />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {real.positions.map((p) => (
                      <TableRow key={p.symbol}>
                        <TableCell className="font-medium">{p.symbol}</TableCell>
                        <TableCell>{p.qty}</TableCell>
                        <TableCell>{fmtMoney2(p.avg_entry_price)}</TableCell>
                        <TableCell>{fmtMoney2(p.price)}</TableCell>
                        <TableCell>{fmtMoney(p.value)}</TableCell>
                        <TableCell className={pnlColor(p.unrealized_pnl)}>
                          {fmtMoney(p.unrealized_pnl)} ({fmtPct(p.unrealized_pnl_pct, 1)})
                        </TableCell>
                        <TableCell><SignalChips s={signals[p.symbol]} /></TableCell>
                        <TableCell>
                          <button onClick={() => removeHolding(p.symbol)} title="Remove"
                            className="text-zinc-400 hover:text-red-600">✕</button>
                        </TableCell>
                      </TableRow>
                    ))}
                    {real.positions.length === 0 && (
                      <TableRow><TableCell colSpan={8} className="text-zinc-400">
                        No holdings yet — add what you actually own below.
                      </TableCell></TableRow>
                    )}
                  </TableBody>
                </Table>
                <form onSubmit={addHolding} className="flex flex-wrap items-end gap-2 border-t pt-3">
                  <label className="text-sm">Symbol
                    <Input value={hSymbol} onChange={(e) => setHSymbol(e.target.value.toUpperCase())}
                      placeholder="AAPL" className="mt-1 w-28" required /></label>
                  <label className="text-sm">Quantity
                    <Input value={hQty} onChange={(e) => setHQty(e.target.value)} type="number"
                      step="any" min="0" className="mt-1 w-28" required /></label>
                  <label className="text-sm">Avg entry price
                    <Input value={hPrice} onChange={(e) => setHPrice(e.target.value)} type="number"
                      step="any" min="0" className="mt-1 w-32" required /></label>
                  <Button type="submit" variant="outline" className="border-emerald-500">Add / update holding</Button>
                  <span className="text-xs text-zinc-500">Adding an existing symbol updates it.</span>
                </form>
              </CardContent>
            </Card>
          </>
        )}
      </section>

      {report && (
        <Card>
          <CardHeader><CardTitle className="text-base">Latest AI briefing (paper portfolio)</CardTitle></CardHeader>
          <CardContent><div className="text-sm whitespace-pre-wrap">{report.narrative}</div></CardContent>
        </Card>
      )}
    </div>
  );
}
