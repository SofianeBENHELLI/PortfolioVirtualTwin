"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { RiskSlider } from "@/components/risk-widgets";

type Version = { id: number; version: number; comment: string; created_at: string };
type RiskLimits = {
  max_position_weight_pct: number; max_sector_weight_pct: number;
  max_portfolio_drawdown_pct: number; max_daily_loss_pct: number;
  max_number_of_positions: number; max_orders_per_day: number;
  rebalance_frequency: string;
};
type StrategyDetail = {
  id: number; name: string; active_version_id: number;
  active_version: { version: number; yaml: string; twin: { risk_management: RiskLimits } & Record<string, unknown> } | null;
  versions: Version[];
  backtest_coverage?: { backtestable: string[]; agent_evaluated: string[] };
};

export default function StrategyDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [detail, setDetail] = useState<StrategyDetail | null>(null);
  const [yamlText, setYamlText] = useState("");
  const [comment, setComment] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [risk, setRisk] = useState<RiskLimits | null>(null);
  const [riskSaving, setRiskSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api<StrategyDetail>(`/api/strategies/${id}`);
      setDetail(d);
      setYamlText(d.active_version?.yaml ?? "");
      if (d.active_version) setRisk({ ...d.active_version.twin.risk_management });
    } catch (e) { setError(e instanceof Error ? e.message : "load failed"); }
  }, [id]);
  useEffect(() => { load(); }, [load]);

  async function saveRiskLimits() {
    if (!detail?.active_version || !risk) return;
    setRiskSaving(true); setError("");
    try {
      const twin = { ...detail.active_version.twin, risk_management: risk };
      await api(`/api/strategies/${id}/versions`, {
        method: "POST",
        body: JSON.stringify({ twin, comment: "risk limits adjusted (sliders)" }),
      });
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "save failed"); }
    finally { setRiskSaving(false); }
  }

  async function saveVersion() {
    setError(""); setSaved(false);
    try {
      await api(`/api/strategies/${id}/versions/yaml`, {
        method: "POST", body: JSON.stringify({ yaml_text: yamlText, comment }),
      });
      setSaved(true);
      setComment("");
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "save failed"); }
  }

  if (!detail) return <div className="text-zinc-500">{error || "Loading…"}</div>;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">{detail.name}
        <Badge className="ml-3 align-middle">active v{detail.active_version?.version}</Badge>
      </h1>

      <div className="grid lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle className="text-base">Strategy Twin (YAML) — saving creates a new immutable version</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <Textarea rows={26} value={yamlText} onChange={(e) => setYamlText(e.target.value)}
              className="font-mono text-xs" />
            <div className="flex gap-3">
              <Input placeholder="what changed? (version comment)" value={comment}
                onChange={(e) => setComment(e.target.value)} />
              <Button onClick={saveVersion}>Save as new version</Button>
            </div>
            {saved && <p className="text-sm text-emerald-600">Saved — new version is now active.</p>}
            {error && <p className="text-sm text-red-600">{error}</p>}
          </CardContent>
        </Card>

        <div className="space-y-6">
          {risk && (
            <Card>
              <CardHeader><CardTitle className="text-base">Risk limits</CardTitle></CardHeader>
              <CardContent className="space-y-4">
                <RiskSlider label="Max position weight" value={risk.max_position_weight_pct}
                  onChange={(v) => setRisk({ ...risk, max_position_weight_pct: v })} min={1} max={25} />
                <RiskSlider label="Max sector weight" value={risk.max_sector_weight_pct}
                  onChange={(v) => setRisk({ ...risk, max_sector_weight_pct: v })} min={10} max={100} step={5} />
                <RiskSlider label="Max portfolio drawdown" value={risk.max_portfolio_drawdown_pct}
                  onChange={(v) => setRisk({ ...risk, max_portfolio_drawdown_pct: v })} min={5} max={40} />
                <RiskSlider label="Max daily loss" value={risk.max_daily_loss_pct}
                  onChange={(v) => setRisk({ ...risk, max_daily_loss_pct: v })} min={1} max={10} step={0.5} />
                <RiskSlider label="Max positions" value={risk.max_number_of_positions}
                  onChange={(v) => setRisk({ ...risk, max_number_of_positions: v })} min={1} max={50} unit="" />
                <RiskSlider label="Max orders / day" value={risk.max_orders_per_day}
                  onChange={(v) => setRisk({ ...risk, max_orders_per_day: v })} min={1} max={50} unit="" />
                <Button onClick={saveRiskLimits} disabled={riskSaving} className="w-full" variant="outline">
                  {riskSaving ? "Saving…" : "Save limits as new version"}
                </Button>
                <p className="text-xs text-zinc-500">Enforced by the risk gateway on every order, paper and real.</p>
              </CardContent>
            </Card>
          )}
          {detail.backtest_coverage && (
            <Card>
              <CardHeader><CardTitle className="text-base">Rule coverage</CardTitle></CardHeader>
              <CardContent className="text-sm space-y-2">
                <p className="font-medium text-emerald-700">Backtestable (price-based):</p>
                <ul className="list-disc pl-5 text-zinc-600">
                  {detail.backtest_coverage.backtestable.map((r) => <li key={r} className="font-mono text-xs">{r}</li>)}
                </ul>
                <p className="font-medium text-amber-700 pt-2">Agent-evaluated at proposal time:</p>
                <ul className="list-disc pl-5 text-zinc-600">
                  {detail.backtest_coverage.agent_evaluated.map((r) => <li key={r} className="font-mono text-xs">{r}</li>)}
                  {detail.backtest_coverage.agent_evaluated.length === 0 && <li>none</li>}
                </ul>
              </CardContent>
            </Card>
          )}
          <Card>
            <CardHeader><CardTitle className="text-base">Version history</CardTitle></CardHeader>
            <CardContent>
              <ul className="space-y-2 text-sm">
                {detail.versions.map((v) => (
                  <li key={v.id} className="flex gap-2">
                    <Badge variant={v.id === detail.active_version_id ? "default" : "outline"}>v{v.version}</Badge>
                    <span className="text-zinc-600">{v.comment || "—"}</span>
                    <span className="ml-auto text-xs text-zinc-400">{new Date(v.created_at).toLocaleDateString()}</span>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
