"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ListPlus, Radar, Telescope } from "lucide-react";

import { RiskGauge } from "@/components/risk-widgets";

type Rec = {
  id: number; symbol: string; action: string; confidence: number; risk_score: number | null;
  target_growth_pct?: number; risk_level?: string; strategic_themes?: string[]; horizon_years?: number;
  thesis: string;
  invalidation: string; data_used: { scores?: Record<string, unknown>; key_points?: string[] }; created_at: string;
};
type Run = {
  id: number; graph: string; status: string; summary: string; error: string;
  prompt_tokens: number; completion_tokens: number; started_at: string;
};
type DiscoveryRun = {
  id: number; recommendations: Rec[]; added_to_watchlist?: string[];
  selected_themes?: string[]; horizon_years?: number;
};

const ACTION_COLOR: Record<string, string> = {
  buy: "bg-emerald-600",
  sell: "bg-red-600",
  hold: "bg-zinc-500",
  track: "bg-sky-600",
};

function csv(v: string): string[] {
  return v.split(",").map((s) => s.trim()).filter(Boolean);
}

export default function ResearchDesk() {
  const [llm, setLlm] = useState(true);
  const [strategies, setStrategies] = useState<{ id: number; name: string }[]>([]);
  const [recs, setRecs] = useState<Rec[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [strategyId, setStrategyId] = useState("");
  const [symbols, setSymbols] = useState("");
  const [busy, setBusy] = useState(false);
  const [propBusy, setPropBusy] = useState(false);
  const [discoveryBusy, setDiscoveryBusy] = useState(false);
  const [strategicBusy, setStrategicBusy] = useState(false);
  const [discoverySymbols, setDiscoverySymbols] = useState("");
  const [discoveryLimit, setDiscoveryLimit] = useState("20");
  const [discoveryWatchlist, setDiscoveryWatchlist] = useState(true);
  const [strategicThemes, setStrategicThemes] = useState("AI, semiconductors, military");
  const [strategicSymbols, setStrategicSymbols] = useState("");
  const [strategicLimit, setStrategicLimit] = useState("20");
  const [strategicHorizon, setStrategicHorizon] = useState("5");
  const [strategicWatchlist, setStrategicWatchlist] = useState(true);
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
          symbols: csv(symbols),
        }),
      });
      setInfo("Research complete - recommendations below.");
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
        ? `${r.created_proposal_ids.length} proposal(s) created and risk-checked - review them in the Trading Console.`
        : "No proposals created (no recent high-confidence buy/sell recommendations).");
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
    finally { setPropBusy(false); }
  }

  async function runStockDiscovery() {
    setDiscoveryBusy(true); setError(""); setInfo("");
    try {
      const body = {
        strategy_id: strategyId ? parseInt(strategyId) : null,
        symbols: csv(discoverySymbols),
        limit: parseInt(discoveryLimit) || 20,
        add_to_watchlist: discoveryWatchlist,
      };
      const r = await api<DiscoveryRun>("/api/agents/stock-discovery", {
        method: "POST", body: JSON.stringify(body),
      });
      const added = r.added_to_watchlist?.length ?? 0;
      setInfo(`Stock Discovery proposed ${r.recommendations.length} tracker(s)${added ? ` and added ${added} to My Stocks` : ""}.`);
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
    finally { setDiscoveryBusy(false); }
  }

  async function runStrategicDiscovery() {
    setStrategicBusy(true); setError(""); setInfo("");
    try {
      const body = {
        themes: csv(strategicThemes),
        symbols: csv(strategicSymbols),
        limit: parseInt(strategicLimit) || 20,
        horizon_years: parseInt(strategicHorizon) || 5,
        add_to_watchlist: strategicWatchlist,
      };
      const r = await api<DiscoveryRun>("/api/agents/strategic-discovery", {
        method: "POST", body: JSON.stringify(body),
      });
      const added = r.added_to_watchlist?.length ?? 0;
      const themes = r.selected_themes?.join(", ") || "selected themes";
      setInfo(`Strategic Discovery ranked ${r.recommendations.length} long-term candidate(s) across ${themes}${added ? ` and added ${added} to My Stocks` : ""}.`);
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
    finally { setStrategicBusy(false); }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Agent Research Desk</h1>
      {!llm && (
        <p className="text-sm text-amber-700 bg-amber-50 rounded p-3">
          Agents are disabled: add your OpenAI key in Settings (each user brings their own). Everything else keeps working.
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
            {busy ? "Researching..." : "Run research agent"}
          </Button>
          <Button variant="outline" onClick={generateProposals} disabled={propBusy || recs.length === 0}>
            {propBusy ? "Working..." : "Generate order proposals"}
          </Button>
        </CardContent>
      </Card>

      <div className="grid xl:grid-cols-2 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <Radar className="h-4 w-4" /> Stock Discovery Agent
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid sm:grid-cols-[1fr_6rem] gap-3">
              <label className="text-sm">Universe override
                <Input value={discoverySymbols} onChange={(e) => setDiscoverySymbols(e.target.value)}
                  placeholder="Optional: NVDA, AMD, MSFT" className="mt-1" />
              </label>
              <label className="text-sm">Limit
                <Input value={discoveryLimit} onChange={(e) => setDiscoveryLimit(e.target.value)}
                  inputMode="numeric" className="mt-1" />
              </label>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={discoveryWatchlist}
                  onChange={(e) => setDiscoveryWatchlist(e.target.checked)} />
                Add picks to My Stocks
              </label>
              <Button onClick={runStockDiscovery} disabled={discoveryBusy}>
                <ListPlus className="h-4 w-4" />
                {discoveryBusy ? "Scanning..." : "Find 20 stocks to track"}
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <Telescope className="h-4 w-4" /> Strategic Discovery Agent
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid sm:grid-cols-[1fr_6rem_6rem] gap-3">
              <label className="text-sm">Long-term themes
                <Input value={strategicThemes} onChange={(e) => setStrategicThemes(e.target.value)}
                  placeholder="AI, semiconductors, military" className="mt-1" />
              </label>
              <label className="text-sm">Years
                <Input value={strategicHorizon} onChange={(e) => setStrategicHorizon(e.target.value)}
                  inputMode="numeric" className="mt-1" />
              </label>
              <label className="text-sm">Limit
                <Input value={strategicLimit} onChange={(e) => setStrategicLimit(e.target.value)}
                  inputMode="numeric" className="mt-1" />
              </label>
            </div>
            <label className="text-sm block">Universe override
              <Input value={strategicSymbols} onChange={(e) => setStrategicSymbols(e.target.value)}
                placeholder="Optional: LMT, RTX, NVDA" className="mt-1" />
            </label>
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={strategicWatchlist}
                  onChange={(e) => setStrategicWatchlist(e.target.checked)} />
                Add picks to My Stocks
              </label>
              <Button onClick={runStrategicDiscovery} disabled={strategicBusy}>
                <Telescope className="h-4 w-4" />
                {strategicBusy ? "Ranking..." : "Rank strategic assets"}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>

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
                  {r.target_growth_pct != null && (
                    <Badge variant="outline">target {r.target_growth_pct.toFixed(1)}%</Badge>
                  )}
                  {r.risk_level && <Badge variant="outline">{r.risk_level} risk</Badge>}
                  {r.horizon_years && <Badge variant="outline">{r.horizon_years}y</Badge>}
                  <RiskGauge score={r.risk_score} compact />
                  <span className="ml-auto text-xs text-zinc-400">{new Date(r.created_at).toLocaleString()}</span>
                </div>
                <p className="text-sm">{r.thesis}</p>
                {r.strategic_themes && r.strategic_themes.length > 0 && (
                  <p className="text-xs text-sky-700">{r.strategic_themes.join(" | ")}</p>
                )}
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
