"use client";

import { useCallback, useEffect, useState } from "react";
import { api, fmtMoney2 } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type RiskCheck = { name: string; passed: boolean; detail: string; observed: string; limit: string };
type Proposal = {
  id: number; symbol: string; side: string; qty: number; order_type: string;
  limit_price: number | null; status: string; risk_passed: boolean | null;
  rationale: string; source: string; created_at: string; risk_checks: RiskCheck[];
};
type Order = {
  id: number; symbol: string; side: string; qty: number; status: string; broker: string;
  order_type: string; filled_qty: number; filled_avg_price: number | null; created_at: string;
};
type Execution = { id: number; event: string; detail: Record<string, unknown>; created_at: string };
type PortfolioRow = { id: number; kind: string; name: string; broker: string; strategy_id: number | null };

const STATUS_COLOR: Record<string, string> = {
  risk_passed: "bg-blue-600", risk_blocked: "bg-red-600", approved: "bg-indigo-600",
  submitted: "bg-amber-500", filled: "bg-emerald-600", rejected: "bg-zinc-500",
  broker_rejected: "bg-red-700", open: "bg-amber-500", cancelled: "bg-zinc-400",
};

function StatusBadge({ s }: { s: string }) {
  return <Badge className={STATUS_COLOR[s] ?? "bg-zinc-500"}>{s}</Badge>;
}

export default function TradingConsole() {
  const [portfolio, setPortfolio] = useState<PortfolioRow | null>(null);
  const [strategies, setStrategies] = useState<{ id: number; name: string }[]>([]);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");

  // order ticket
  const [symbol, setSymbol] = useState("");
  const [side, setSide] = useState("buy");
  const [qty, setQty] = useState("10");
  const [orderType, setOrderType] = useState("market");
  const [limitPrice, setLimitPrice] = useState("");
  const [rationale, setRationale] = useState("");

  // new portfolio form
  const [npBroker, setNpBroker] = useState("sim");
  const [npCash, setNpCash] = useState("100000");
  const [npStrategy, setNpStrategy] = useState("");

  const load = useCallback(async () => {
    try {
      const ps = await api<PortfolioRow[]>("/api/portfolios");
      setStrategies(await api("/api/strategies"));
      const papers = ps.filter((x) => x.kind === "paper");
      if (papers.length === 0) { setPortfolio(null); return; }
      const p = papers[0];
      setPortfolio(p);
      setProposals(await api(`/api/portfolios/${p.id}/proposals`));
      setOrders(await api(`/api/portfolios/${p.id}/orders`));
      setExecutions(await api(`/api/portfolios/${p.id}/executions`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "load failed");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 15_000);
    return () => clearInterval(t);
  }, [load]);

  async function createPortfolio(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("/api/portfolios", {
        method: "POST",
        body: JSON.stringify({
          name: "Paper Portfolio", broker: npBroker,
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
        ? "Proposal passed all risk checks — approve it below to execute."
        : "Proposal was BLOCKED by the risk gateway — expand it below to see why.");
      setExpanded(p.id);
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
  }

  async function decideOn(id: number, decision: string) {
    if (!portfolio) return;
    setError("");
    try {
      await api(`/api/portfolios/${portfolio.id}/proposals/${id}/decision`, {
        method: "POST", body: JSON.stringify({ decision, note: "" }),
      });
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
  }

  if (portfolio === null)
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
            {strategies.length === 0 && (
              <p className="text-xs text-zinc-500">You need a strategy first — create one under Strategies.</p>
            )}
            {error && <p className="text-sm text-red-600">{error}</p>}
          </form>
        </CardContent>
      </Card>
    );

  const pending = proposals.filter((p) => p.status === "risk_passed");
  const others = proposals.filter((p) => p.status !== "risk_passed");

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Paper Trading Console
        <Badge className="ml-3 align-middle bg-amber-500 text-black hover:bg-amber-500">PAPER ONLY</Badge>
        <span className="ml-3 text-sm font-normal text-zinc-500">broker: {portfolio.broker}</span>
      </h1>
      <p className="text-sm text-zinc-500 -mt-4">
        Orders here only ever touch your simulated portfolio. Your real (tracked) portfolio is read-only by design.
      </p>

      <Card>
        <CardHeader><CardTitle className="text-base">New order proposal</CardTitle></CardHeader>
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
            {pending.map((p) => <ProposalRow key={p.id} p={p} expanded={expanded === p.id}
              onToggle={() => setExpanded(expanded === p.id ? null : p.id)} onDecide={decideOn} />)}
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
              onToggle={() => setExpanded(expanded === p.id ? null : p.id)} onDecide={decideOn} />)}
            {others.length === 0 && <p className="text-sm text-zinc-400">Nothing yet</p>}
          </CardContent></Card>
        </TabsContent>
        <TabsContent value="orders">
          <Card><CardContent className="pt-4">
            <Table>
              <TableHeader><TableRow>
                <TableHead>Symbol</TableHead><TableHead>Side</TableHead><TableHead>Qty</TableHead>
                <TableHead>Type</TableHead><TableHead>Status</TableHead><TableHead>Fill</TableHead>
                <TableHead>Broker</TableHead><TableHead>Time</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {orders.map((o) => (
                  <TableRow key={o.id}>
                    <TableCell className="font-medium">{o.symbol}</TableCell>
                    <TableCell>{o.side}</TableCell><TableCell>{o.qty}</TableCell>
                    <TableCell>{o.order_type}</TableCell>
                    <TableCell><StatusBadge s={o.status} /></TableCell>
                    <TableCell>{o.filled_avg_price ? `${o.filled_qty} @ ${fmtMoney2(o.filled_avg_price)}` : "—"}</TableCell>
                    <TableCell>{o.broker}</TableCell>
                    <TableCell className="text-xs text-zinc-500">{new Date(o.created_at).toLocaleString()}</TableCell>
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

function ProposalRow({ p, expanded, onToggle, onDecide }: {
  p: Proposal; expanded: boolean; onToggle: () => void;
  onDecide: (id: number, d: string) => void;
}) {
  return (
    <div className="border rounded-lg p-3 bg-white">
      <div className="flex items-center gap-3">
        <StatusBadge s={p.status} />
        <span className="font-medium">{p.side.toUpperCase()} {p.qty} {p.symbol}</span>
        <span className="text-sm text-zinc-500">{p.order_type}{p.limit_price ? ` @ ${fmtMoney2(p.limit_price)}` : ""}</span>
        <Badge variant="outline">{p.source}</Badge>
        <button onClick={onToggle} className="ml-auto text-sm text-blue-600 hover:underline">
          {expanded ? "Hide ticket" : "Explain ticket"}
        </button>
        {p.status === "risk_passed" && (
          <>
            <Button size="sm" onClick={() => onDecide(p.id, "approved")}>Approve & execute</Button>
            <Button size="sm" variant="outline" onClick={() => onDecide(p.id, "rejected")}>Reject</Button>
          </>
        )}
      </div>
      {expanded && (
        <div className="mt-3 space-y-2 text-sm">
          <p><span className="font-medium">Why:</span> {p.rationale || "—"}</p>
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
