"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, fmtMoney2 } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { RiskFactorList, RiskGauge } from "@/components/risk-widgets";

type RiskCheck = { name: string; passed: boolean; detail: string; observed: string; limit: string };
type Proposal = {
  id: number; symbol: string; side: string; qty: number; order_type: string;
  limit_price: number | null; status: string; risk_passed: boolean | null;
  rationale: string; source: string; created_at: string; risk_checks: RiskCheck[];
  risk_score: number | null;
  risk_factors: { band?: string; factors?: Record<string, { utilization: number; detail: string }> };
};
type Order = {
  id: number; symbol: string; side: string; qty: number; status: string; broker: string;
  order_type: string; filled_qty: number; filled_avg_price: number | null; created_at: string;
};
type Execution = { id: number; event: string; detail: Record<string, unknown>; created_at: string };
type PortfolioRow = {
  id: number; kind: string; name: string; broker: string; strategy_id: number | null;
  live_armed: boolean; max_order_notional: number; max_live_orders_per_day: number;
};

const STATUS_COLOR: Record<string, string> = {
  risk_passed: "bg-blue-600", risk_blocked: "bg-red-600", approved: "bg-indigo-600",
  submitted: "bg-amber-500", filled: "bg-emerald-600", rejected: "bg-zinc-500",
  broker_rejected: "bg-red-700", open: "bg-amber-500", cancelled: "bg-zinc-400",
};

function StatusBadge({ s }: { s: string }) {
  return <Badge className={STATUS_COLOR[s] ?? "bg-zinc-500"}>{s}</Badge>;
}

export default function TradingConsole() {
  const [portfolios, setPortfolios] = useState<PortfolioRow[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [strategies, setStrategies] = useState<{ id: number; name: string }[]>([]);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");

  // order ticket
  const [symbol, setSymbol] = useState("");
  const [side, setSide] = useState("buy");
  const [qty, setQty] = useState("10");
  const [orderType, setOrderType] = useState("market");
  const [limitPrice, setLimitPrice] = useState("");
  const [rationale, setRationale] = useState("");

  // new paper portfolio form
  const [npBroker, setNpBroker] = useState("sim");
  const [npCash, setNpCash] = useState("100000");
  const [npStrategy, setNpStrategy] = useState("");

  const portfolio = portfolios.find((p) => p.id === selectedId) ?? null;
  const isReal = portfolio?.kind === "real_tracked";

  const load = useCallback(async () => {
    try {
      const ps = await api<PortfolioRow[]>("/api/portfolios");
      setPortfolios(ps);
      setStrategies(await api("/api/strategies"));
      setSelectedId((cur) => cur ?? ps.find((x) => x.kind === "paper")?.id ?? ps[0]?.id ?? null);
    } catch (e) { setError(e instanceof Error ? e.message : "load failed"); }
  }, []);

  const loadDetail = useCallback(async () => {
    if (selectedId == null) return;
    try {
      setProposals(await api(`/api/portfolios/${selectedId}/proposals`));
      setOrders(await api(`/api/portfolios/${selectedId}/orders`));
      setExecutions(await api(`/api/portfolios/${selectedId}/executions`));
    } catch (e) { setError(e instanceof Error ? e.message : "load failed"); }
  }, [selectedId]);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    loadDetail();
    const t = setInterval(loadDetail, 15_000);
    return () => clearInterval(t);
  }, [loadDetail]);

  async function createPortfolio(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("/api/portfolios", {
        method: "POST",
        body: JSON.stringify({
          name: "Paper Portfolio", broker: npBroker, kind: "paper",
          initial_cash: parseFloat(npCash),
          strategy_id: npStrategy ? parseInt(npStrategy) : null,
        }),
      });
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
  }

  async function propose(e: React.FormEvent) {
    e.preventDefault();
    if (!portfolio) return;
    setError(""); setInfo("");
    try {
      const p = await api<Proposal>(`/api/portfolios/${portfolio.id}/proposals`, {
        method: "POST",
        body: JSON.stringify({
          symbol, side, qty: parseFloat(qty), order_type: orderType,
          limit_price: orderType === "limit" ? parseFloat(limitPrice) : null,
          rationale,
        }),
      });
      setInfo(p.status === "risk_passed"
        ? `Risk score ${p.risk_score?.toFixed(0) ?? "—"}/100 — all checks passed. Approve below to execute.`
        : "Proposal BLOCKED by the risk gateway — expand it to see why.");
      setExpanded(p.id);
      loadDetail();
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
  }

  async function decideOn(id: number, decision: string) {
    if (!portfolio) return;
    setError("");
    try {
      await api(`/api/portfolios/${portfolio.id}/proposals/${id}/decision`, {
        method: "POST",
        body: JSON.stringify({ decision, note: "", confirm_text: confirmText }),
      });
      setConfirmText("");
      if (decision === "approved" && isReal) {
        setInfo("Approved. Execute the order at your broker, then record the fill in the Orders tab.");
      }
      loadDetail();
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
  }

  async function recordFill(orderId: number, fqty: string, fprice: string) {
    if (!portfolio) return;
    setError("");
    try {
      await api(`/api/portfolios/${portfolio.id}/orders/${orderId}/record-fill`, {
        method: "POST",
        body: JSON.stringify({ filled_qty: parseFloat(fqty), fill_price: parseFloat(fprice) }),
      });
      setInfo("Fill recorded — position and P&L updated.");
      loadDetail();
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
  }

  async function cancelOrder(orderId: number) {
    if (!portfolio) return;
    await api(`/api/portfolios/${portfolio.id}/orders/${orderId}/cancel`, { method: "POST" });
    loadDetail();
  }

  if (portfolios.length === 0)
    return (
      <Card className="max-w-md">
        <CardHeader><CardTitle>Create your paper portfolio</CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={createPortfolio} className="space-y-3">
            <label className="block text-sm">Broker
              <select value={npBroker} onChange={(e) => setNpBroker(e.target.value)}
                className="mt-1 w-full border rounded-md h-9 px-2 text-sm">
                <option value="sim">Internal simulator (no signup needed)</option>
                <option value="alpaca_paper">Alpaca paper (needs API keys on backend)</option>
              </select>
            </label>
            <label className="block text-sm">Initial cash
              <Input value={npCash} onChange={(e) => setNpCash(e.target.value)} type="number" className="mt-1" />
            </label>
            <label className="block text-sm">Strategy
              <select value={npStrategy} onChange={(e) => setNpStrategy(e.target.value)}
                className="mt-1 w-full border rounded-md h-9 px-2 text-sm" required>
                <option value="">— pick a strategy —</option>
                {strategies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </label>
            <Button type="submit" className="w-full">Create paper portfolio</Button>
            {error && <p className="text-sm text-red-600">{error}</p>}
          </form>
        </CardContent>
      </Card>
    );

  const pending = proposals.filter((p) => p.status === "risk_passed");
  const others = proposals.filter((p) => p.status !== "risk_passed");
  const theme = isReal ? "border-red-300 bg-red-50/30" : "border-amber-300 bg-amber-50/30";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold">Trading Console</h1>
        <div className="flex rounded-lg border overflow-hidden">
          {portfolios.map((p) => (
            <button key={p.id} onClick={() => { setSelectedId(p.id); setExpanded(null); }}
              className={`px-3 py-1.5 text-sm font-medium ${
                p.id === selectedId
                  ? p.kind === "paper" ? "bg-amber-400 text-black" : "bg-red-600 text-white"
                  : "bg-white text-zinc-600 hover:bg-zinc-50"}`}>
              {p.kind === "paper" ? "🟡 Paper" : "🔴 Real"}
            </button>
          ))}
        </div>
        {portfolio && (
          <span className="text-sm text-zinc-500">
            {portfolio.name} · broker: {portfolio.broker}
            {isReal && (portfolio.live_armed
              ? ` · armed · max ${fmtMoney2(portfolio.max_order_notional)}/order`
              : " · NOT ARMED")}
          </span>
        )}
      </div>

      {isReal && !portfolio?.live_armed && (
        <Card className="border-red-300">
          <CardContent className="pt-4 text-sm flex items-center gap-3">
            <span>🔒 This real portfolio is <b>not armed</b> — the risk gateway blocks every order.
              Arm it from the Portfolio page after passing the readiness checklist.</span>
            <Link href="/" className="text-blue-600 hover:underline shrink-0">Go to Portfolio →</Link>
          </CardContent>
        </Card>
      )}
      {isReal && portfolio?.live_armed && (
        <p className="text-sm rounded-md border border-red-300 bg-red-50 p-3">
          🔴 <b>REAL portfolio.</b> Approved orders are <b>your</b> orders: the app never sends them to a
          broker API — you execute them at your broker, then record the fill here. Typed CONFIRM required.
        </p>
      )}

      <Card className={theme}>
        <CardHeader><CardTitle className="text-base">
          New order proposal {isReal ? "(real)" : "(paper)"}
        </CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={propose} className="flex flex-wrap items-end gap-3">
            <label className="text-sm">Symbol
              <Input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                placeholder="AAPL" className="mt-1 w-28" required />
            </label>
            <label className="text-sm">Side
              <select value={side} onChange={(e) => setSide(e.target.value)}
                className="mt-1 block border rounded-md h-9 px-2 text-sm">
                <option value="buy">buy</option><option value="sell">sell</option>
              </select>
            </label>
            <label className="text-sm">Qty
              <Input value={qty} onChange={(e) => setQty(e.target.value)} type="number"
                step="any" className="mt-1 w-24" required />
            </label>
            <label className="text-sm">Type
              <select value={orderType} onChange={(e) => setOrderType(e.target.value)}
                className="mt-1 block border rounded-md h-9 px-2 text-sm">
                <option value="market">market</option><option value="limit">limit</option>
              </select>
            </label>
            {orderType === "limit" && (
              <label className="text-sm">Limit price
                <Input value={limitPrice} onChange={(e) => setLimitPrice(e.target.value)}
                  type="number" step="any" className="mt-1 w-28" required />
              </label>
            )}
            <label className="text-sm flex-1 min-w-48">Why this trade?
              <Input value={rationale} onChange={(e) => setRationale(e.target.value)}
                placeholder="your reasoning (kept in the audit trail)" className="mt-1" />
            </label>
            <Button type="submit">Run risk checks</Button>
          </form>
          {info && <p className="mt-2 text-sm text-blue-700">{info}</p>}
          {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
        </CardContent>
      </Card>

      {pending.length > 0 && (
        <Card className="border-blue-300">
          <CardHeader><CardTitle className="text-base">Awaiting your approval ({pending.length})</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            {isReal && (
              <div className="flex items-center gap-2 text-sm">
                <span>Type</span><code className="rounded bg-zinc-100 px-1">CONFIRM</code>
                <span>to enable approval of real orders:</span>
                <Input value={confirmText} onChange={(e) => setConfirmText(e.target.value)}
                  placeholder="CONFIRM" className="w-32 h-8" />
              </div>
            )}
            {pending.map((p) => <ProposalRow key={p.id} p={p} expanded={expanded === p.id}
              onToggle={() => setExpanded(expanded === p.id ? null : p.id)} onDecide={decideOn}
              approveDisabled={isReal && confirmText !== "CONFIRM"} />)}
          </CardContent>
        </Card>
      )}

      <Tabs defaultValue="proposals">
        <TabsList>
          <TabsTrigger value="proposals">All proposals</TabsTrigger>
          <TabsTrigger value="orders">Orders</TabsTrigger>
          <TabsTrigger value="executions">Execution log</TabsTrigger>
        </TabsList>
        <TabsContent value="proposals">
          <Card><CardContent className="pt-4 space-y-3">
            {others.map((p) => <ProposalRow key={p.id} p={p} expanded={expanded === p.id}
              onToggle={() => setExpanded(expanded === p.id ? null : p.id)} onDecide={decideOn}
              approveDisabled />)}
            {others.length === 0 && <p className="text-sm text-zinc-400">Nothing yet</p>}
          </CardContent></Card>
        </TabsContent>
        <TabsContent value="orders">
          <Card><CardContent className="pt-4">
            <Table>
              <TableHeader><TableRow>
                <TableHead>Symbol</TableHead><TableHead>Side</TableHead><TableHead>Qty</TableHead>
                <TableHead>Type</TableHead><TableHead>Status</TableHead><TableHead>Fill</TableHead>
                <TableHead>Broker</TableHead><TableHead>Action</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {orders.map((o) => (
                  <TableRow key={o.id}>
                    <TableCell className="font-medium">{o.symbol}</TableCell>
                    <TableCell>{o.side}</TableCell><TableCell>{o.qty}</TableCell>
                    <TableCell>{o.order_type}</TableCell>
                    <TableCell>
                      <StatusBadge s={o.status} />
                      {o.broker === "manual" && o.status === "open" &&
                        <span className="ml-1 text-xs text-red-600">execute at your broker</span>}
                    </TableCell>
                    <TableCell>{o.filled_avg_price ? `${o.filled_qty} @ ${fmtMoney2(o.filled_avg_price)}` : "—"}</TableCell>
                    <TableCell>{o.broker}</TableCell>
                    <TableCell>
                      {o.broker === "manual" && o.status === "open" && (
                        <RecordFillForm onSubmit={(q, pr) => recordFill(o.id, q, pr)}
                          onCancel={() => cancelOrder(o.id)} defaultQty={String(o.qty)} />
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent></Card>
        </TabsContent>
        <TabsContent value="executions">
          <Card><CardContent className="pt-4">
            <Table>
              <TableHeader><TableRow>
                <TableHead>Event</TableHead><TableHead>Detail</TableHead><TableHead>Time</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {executions.map((e) => (
                  <TableRow key={e.id}>
                    <TableCell><StatusBadge s={e.event} /></TableCell>
                    <TableCell className="text-xs font-mono">{JSON.stringify(e.detail)}</TableCell>
                    <TableCell className="text-xs text-zinc-500">{new Date(e.created_at).toLocaleString()}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent></Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

function RecordFillForm({ onSubmit, onCancel, defaultQty }: {
  onSubmit: (qty: string, price: string) => void; onCancel: () => void; defaultQty: string;
}) {
  const [q, setQ] = useState(defaultQty);
  const [pr, setPr] = useState("");
  return (
    <div className="flex items-center gap-1.5">
      <Input value={q} onChange={(e) => setQ(e.target.value)} type="number" step="any"
        className="h-8 w-20" title="filled qty" />
      <Input value={pr} onChange={(e) => setPr(e.target.value)} type="number" step="any"
        placeholder="fill $" className="h-8 w-24" />
      <Button size="sm" className="h-8" disabled={!q || !pr} onClick={() => onSubmit(q, pr)}>
        Record fill
      </Button>
      <button onClick={onCancel} className="text-xs text-zinc-400 hover:text-red-600">cancel order</button>
    </div>
  );
}

function ProposalRow({ p, expanded, onToggle, onDecide, approveDisabled }: {
  p: Proposal; expanded: boolean; onToggle: () => void;
  onDecide: (id: number, d: string) => void; approveDisabled?: boolean;
}) {
  return (
    <div className="border rounded-lg p-3 bg-white">
      <div className="flex items-center gap-3 flex-wrap">
        <StatusBadge s={p.status} />
        <span className="font-medium">{p.side.toUpperCase()} {p.qty} {p.symbol}</span>
        <span className="text-sm text-zinc-500">{p.order_type}{p.limit_price ? ` @ ${fmtMoney2(p.limit_price)}` : ""}</span>
        <RiskGauge score={p.risk_score} band={p.risk_factors?.band} />
        <Badge variant="outline">{p.source}</Badge>
        <button onClick={onToggle} className="ml-auto text-sm text-blue-600 hover:underline">
          {expanded ? "Hide ticket" : "Explain ticket"}
        </button>
        {p.status === "risk_passed" && (
          <>
            <Button size="sm" onClick={() => onDecide(p.id, "approved")} disabled={approveDisabled}
              title={approveDisabled ? "Type CONFIRM above first" : ""}>
              Approve & execute
            </Button>
            <Button size="sm" variant="outline" onClick={() => onDecide(p.id, "rejected")}>Reject</Button>
          </>
        )}
      </div>
      {expanded && (
        <div className="mt-3 space-y-3 text-sm">
          <p><span className="font-medium">Why:</span> {p.rationale || "—"}</p>
          {p.risk_factors?.factors && (
            <div>
              <p className="font-medium mb-1">Risk score breakdown ({p.risk_score?.toFixed(0)}/100 — {p.risk_factors.band}):</p>
              <RiskFactorList factors={p.risk_factors.factors} />
            </div>
          )}
          <div>
            <p className="font-medium mb-1">Risk gateway checks:</p>
            <ul className="space-y-0.5">
              {p.risk_checks.map((c) => (
                <li key={c.name} className="flex gap-2">
                  <span className={c.passed ? "text-emerald-600" : "text-red-600"}>{c.passed ? "✓" : "✗"}</span>
                  <span className="font-mono text-xs pt-0.5">{c.name}</span>
                  <span className="text-zinc-500 text-xs pt-0.5">
                    {c.detail}{c.limit ? ` (observed ${c.observed}, limit ${c.limit})` : ""}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
