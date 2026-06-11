"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type Strategy = { id: number; name: string; active_version: { version: number } | null; created_at: string };

const TEMPLATE = `strategy_name: Quality Growth With Risk Control
universe:
  asset_classes: [stocks, ETFs]
  regions: [US]
  symbols: [AAPL, MSFT, NVDA, GOOGL, AMZN, JPM, XOM, JNJ]
  exclusions: []
investment_thesis:
  style: quality growth
  horizon: 3 to 18 months
  description: Strong fundamentals, positive momentum, controlled valuation risk.
entry_rules:
  - {metric: momentum_score, op: ">", value: 60}
  - {metric: price_above_200_day_average, op: "==", value: true}
  - {metric: quality_score, op: ">", value: 75}
exit_rules:
  - {metric: drawdown_from_entry, op: ">", value: 12}
  - {metric: price_above_200_day_average, op: "==", value: false}
risk_management:
  max_position_weight_pct: 15
  max_sector_weight_pct: 40
  max_portfolio_drawdown_pct: 15
  max_daily_loss_pct: 3
  max_number_of_positions: 10
  max_orders_per_day: 20
execution:
  mode: paper_trading_only
  broker: sim
  human_approval_required: true
benchmark: SPY
`;

export default function StrategiesPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [yamlText, setYamlText] = useState(TEMPLATE);
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [llm, setLlm] = useState(false);

  const load = useCallback(async () => {
    try {
      setStrategies(await api("/api/strategies"));
      const s = await api<{ llm_available: boolean }>("/api/agents/status");
      setLlm(s.llm_available);
    } catch (e) { setError(e instanceof Error ? e.message : "load failed"); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function createFromYaml() {
    setBusy(true); setError("");
    try {
      await api("/api/strategies/yaml", { method: "POST", body: JSON.stringify({ yaml_text: yamlText }) });
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
    finally { setBusy(false); }
  }

  async function buildWithAI() {
    setBusy(true); setError("");
    try {
      const r = await api<{ yaml: string }>("/api/agents/capture", {
        method: "POST", body: JSON.stringify({ description }),
      });
      setYamlText(r.yaml);
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
    finally { setBusy(false); }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">My Strategy</h1>
        <Link href="/strategies/new">
          <Button className="bg-emerald-700 hover:bg-emerald-800">+ Guided Strategy Builder</Button>
        </Link>
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        {strategies.map((s) => (
          <Link key={s.id} href={`/strategies/${s.id}`}>
            <Card className="hover:border-blue-400 transition-colors">
              <CardContent className="pt-4">
                <div className="font-medium">{s.name}</div>
                <div className="text-sm text-zinc-500">
                  v{s.active_version?.version ?? "—"} · created {new Date(s.created_at).toLocaleDateString()}
                </div>
              </CardContent>
            </Card>
          </Link>
        ))}
        {strategies.length === 0 && <p className="text-sm text-zinc-400">No strategies yet — create one below.</p>}
      </div>

      <Card>
        <CardHeader><CardTitle className="text-base">New Strategy Twin</CardTitle></CardHeader>
        <CardContent>
          <Tabs defaultValue="ai">
            <TabsList>
              <TabsTrigger value="ai">Describe it (AI builder)</TabsTrigger>
              <TabsTrigger value="yaml">YAML editor</TabsTrigger>
            </TabsList>
            <TabsContent value="ai" className="space-y-3">
              {!llm && (
                <p className="text-sm text-amber-700 bg-amber-50 rounded p-2">
                  The AI builder needs your OpenAI key — add it in Settings. You can still use the wizard or YAML editor.
                </p>
              )}
              <Textarea rows={4} value={description} onChange={(e) => setDescription(e.target.value)}
                placeholder="e.g. I want to invest in profitable US tech companies with strong momentum over 6-18 months, never risking more than 10% per position, exiting if a stock drops 15% from my entry…" />
              <Button onClick={buildWithAI} disabled={busy || !llm || !description}>
                {busy ? "Thinking…" : "Draft Strategy Twin → YAML"}
              </Button>
              <p className="text-xs text-zinc-500">The draft lands in the YAML tab for you to review and save — the agent never saves on its own.</p>
            </TabsContent>
            <TabsContent value="yaml" className="space-y-3">
              <Textarea rows={22} value={yamlText} onChange={(e) => setYamlText(e.target.value)}
                className="font-mono text-xs" />
              <Button onClick={createFromYaml} disabled={busy}>Validate & create strategy</Button>
            </TabsContent>
          </Tabs>
          {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
        </CardContent>
      </Card>
    </div>
  );
}
