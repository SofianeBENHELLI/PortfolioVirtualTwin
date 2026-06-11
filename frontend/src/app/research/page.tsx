"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

import { RiskGauge } from "@/components/risk-widgets";

type Rec = {
  id: number; symbol: string; action: string; confidence: number; risk_score: number | null;
  thesis: string;
  invalidation: string; data_used: { scores?: Record<string, unknown> }; created_at: string;
};
type Run = {
  id: number; graph: string; status: string; summary: string; error: string;
  prompt_tokens: number; completion_tokens: number; started_at: string;
};

const ACTION_COLOR: Record<string, string> = { buy: "bg-emerald-600", sell: "bg-red-600", hold: "bg-zinc-500" };

export default function ResearchDesk() {
  const [llm, setLlm] = useState(true);
  const [strategies, setStrategies] = useState<{ id: number; name: string }[]>([]);
  const [recs, setRecs] = useState<Rec[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [strategyId, setStrategyId] = useState("");
  const [symbols, setSymbols] = useState("");
  const [busy, setBusy] = useState(false);
  const [propBusy, setPropBusy] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");

  const load = useCallback(async () => {
    try {
      const s = await api<{ llm_available: boolean }>("/api/agents/status");
      setLlm(s.llm_available);
      setStrategies(await api("/api/strategies"));
      setRecs(await api("/api/agents/recommendations"));
      setRuns(await api("/api/agents/runs"));
    } catch (e) { setError(e instanceof Error ? e.message : "load failed"); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function runResearch() {
    setBusy(true); setError(""); setInfo("");
    try {
      await api("/api/agents/research", {
        method: "POST",
        body: JSON.stringify({
          strategy_id: parseInt(strategyId),
          symbols: symbols ? symbols.split(",").map((s) => s.trim()) : [],
        }),
      });
      setInfo("Research complete — recommendations below.");
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
    finally { setBusy(false); }
  }

  async function generateProposals() {
    setPropBusy(true); setError(""); setInfo("");
    try {
      const portfolios = await api<{ id: number; kind: string }[]>("/api/portfolios");
      const paper = portfolios.find((p) => p.kind === "paper");
      if (!paper) throw new Error("Create a paper portfolio first (Trading Console)");
      const r = await api<{ created_proposal_ids: number[] }>("/api/agents/proposals", {
        method: "POST", body: JSON.stringify({ portfolio_id: paper.id }),
      });
      setInfo(r.created_proposal_ids.length > 0
        ? `${r.created_proposal_ids.length} proposal(s) created and risk-checked — review them in the Trading Console.`
        : "No proposals created (no recent high-confidence buy/sell recommendations).");
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
    finally { setPropBusy(false); }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Agent Research Desk</h1>
      {!llm && (
        <p className="text-sm text-amber-700 bg-amber-50 rounded p-3">
          Agents are disabled: set OPENAI_API_KEY in backend/.env. Everything else keeps working.
        </p>
      )}

      <Card>
        <CardContent className="pt-4 flex flex-wrap items-end gap-3">
          <label className="text-sm">Strategy
            <select value={strategyId} onChange={(e) => setStrategyId(e.target.value)}
              className="mt-1 block border rounded-md h-9 px-2 text-sm min-w-48" required>
              <option value="">— pick —</option>
              {strategies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          <label className="text-sm flex-1 min-w-64">Symbols (optional, defaults to strategy universe, max 10)
            <Input value={symbols} onChange={(e) => setSymbols(e.target.value)}
              placeholder="AAPL, MSFT, NVDA" className="mt-1" />
          </label>
          <Button onClick={runResearch} disabled={busy || !llm || !strategyId}>
            {busy ? "Researching… (can take a minute)" : "Run research agent"}
          </Button>
          <Button variant="outline" onClick={generateProposals} disabled={propBusy || recs.length === 0}>
            {propBusy ? "Working…" : "Generate order proposals"}
          </Button>
        </CardContent>
      </Card>
      {info && <p className="text-sm text-blue-700">{info}</p>}
      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="grid lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-3">
          <h2 className="font-semibold">Recommendations</h2>
          {recs.map((r) => (
            <Card key={r.id}>
              <CardContent className="pt-4 space-y-1">
                <div className="flex items-center gap-2">
                  <Badge className={ACTION_COLOR[r.action] ?? "bg-zinc-500"}>{r.action.toUpperCase()}</Badge>
                  <span className="font-medium">{r.symbol}</span>
                  <span className="text-sm text-zinc-500">confidence {(r.confidence * 100).toFixed(0)}%</span>
                  <RiskGauge score={r.risk_score} compact />
                  <span className="ml-auto text-xs text-zinc-400">{new Date(r.created_at).toLocaleString()}</span>
                </div>
                <p className="text-sm">{r.thesis}</p>
                <p className="text-sm text-amber-700"><span className="font-medium">Invalidation:</span> {r.invalidation}</p>
                {r.data_used?.scores && (
                  <p className="text-xs text-zinc-500 font-mono">{JSON.stringify(r.data_used.scores)}</p>
                )}
              </CardContent>
            </Card>
          ))}
          {recs.length === 0 && <p className="text-sm text-zinc-400">No recommendations yet.</p>}
        </div>

        <div className="space-y-3">
          <h2 className="font-semibold">Agent runs</h2>
          {runs.map((r) => (
            <Card key={r.id}>
              <CardContent className="pt-4 text-sm">
                <div className="flex items-center gap-2">
                  <Badge variant="outline">{r.graph}</Badge>
                  <Badge className={r.status === "done" ? "bg-emerald-600" : r.status === "failed" ? "bg-red-600" : "bg-amber-500"}>
                    {r.status}
                  </Badge>
                </div>
                <p className="mt-1">{r.summary || r.error}</p>
                <p className="text-xs text-zinc-400">
                  {r.prompt_tokens + r.completion_tokens} tokens · {new Date(r.started_at).toLocaleString()}
                </p>
              </CardContent>
            </Card>
          ))}
          {runs.length === 0 && <p className="text-sm text-zinc-400">No runs yet.</p>}
        </div>
      </div>
    </div>
  );
}
