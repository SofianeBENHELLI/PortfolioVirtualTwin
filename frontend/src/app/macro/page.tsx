"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type Indicator = {
  label: string; value: number; chg_1d_pct: number; chg_5d_pct: number; chg_30d_pct: number;
  z_5d: number; above_200d: boolean | null; sparkline: number[];
};
type Snapshot = {
  id: number; as_of: string;
  indicators: Record<string, Indicator>;
  fred: Record<string, number>;
  war: { available: boolean; coverage_pct?: number; z_score?: number;
         headlines?: { title: string; source: string; url: string }[] };
  gpr: { available: boolean; value?: number; percentile_1y?: number };
  regimes: Record<string, unknown>;
};
type Report = { id: number; narrative: string; created_at: string };

const ORDER = ["vix", "sp500", "wti", "brent", "gold", "us10y", "dxy", "eurusd"];

function Sparkline({ values }: { values: number[] }) {
  if (!values || values.length < 2) return null;
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;
  const pts = values.map((v, i) =>
    `${(i / (values.length - 1)) * 100},${30 - ((v - min) / range) * 28}`).join(" ");
  const up = values[values.length - 1] >= values[0];
  return (
    <svg viewBox="0 0 100 32" className="h-8 w-full" preserveAspectRatio="none">
      <polyline points={pts} fill="none" strokeWidth="1.5"
        className={up ? "stroke-emerald-500" : "stroke-red-500"} />
    </svg>
  );
}

function RegimeBadge({ name, value }: { name: string; value: unknown }) {
  let cls = "bg-zinc-200 text-zinc-700 hover:bg-zinc-200";
  let text = `${name}: ${String(value)}`;
  if (value === true || value === "high") cls = "bg-red-600 hover:bg-red-600";
  else if (value === "elevated") cls = "bg-amber-500 hover:bg-amber-500";
  else if (value === false || value === "calm" || value === "low") cls = "bg-emerald-600 hover:bg-emerald-600";
  if (value === null || value === undefined) text = `${name}: n/a`;
  return <Badge className={cls}>{text.replaceAll("_", " ")}</Badge>;
}

export default function MacroPage() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [llm, setLlm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [briefBusy, setBriefBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await api<{ snapshot: Snapshot | null }>("/api/macro");
      setSnap(r.snapshot);
      const reports = await api<Report[]>("/api/macro/reports");
      setReport(reports[0] ?? null);
      const s = await api<{ llm_available: boolean }>("/api/agents/status");
      setLlm(s.llm_available);
    } catch (e) { setError(e instanceof Error ? e.message : "load failed"); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function refresh() {
    setBusy(true); setError("");
    try {
      setSnap(await api<Snapshot>("/api/macro/refresh", { method: "POST" }));
    } catch (e) { setError(e instanceof Error ? e.message : "refresh failed"); }
    finally { setBusy(false); }
  }

  async function brief() {
    setBriefBusy(true); setError("");
    try {
      setReport(await api<Report>("/api/macro/brief", { method: "POST" }));
    } catch (e) { setError(e instanceof Error ? e.message : "brief failed"); }
    finally { setBriefBusy(false); }
  }

  const regimes = snap?.regimes ?? {};
  const regimeKeys = ["volatility_regime", "risk_off", "oil_shock", "gold_rush", "war_risk", "curve_inverted"];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold">Macro & Geopolitics</h1>
        <span className="text-xs text-zinc-400">
          {snap ? `data: ${new Date(snap.as_of).toLocaleString()}` : "no snapshot yet"}
        </span>
        <div className="ml-auto flex gap-2">
          <Button onClick={refresh} disabled={busy}>
            {busy ? "Fetching… (~30s)" : "⟳ Refresh macro data"}
          </Button>
          <Button onClick={brief} disabled={briefBusy || !llm || !snap}
            className="bg-purple-700 hover:bg-purple-800">
            {briefBusy ? "Writing…" : "🌍 Macro agent briefing"}
          </Button>
        </div>
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      {!snap && !busy && (
        <Card><CardContent className="pt-4 text-sm text-zinc-500">
          Press “Refresh macro data” to fetch VIX, oil, gold, rates, the dollar, geopolitical risk
          (GPR index) and global war-news intensity (GDELT) — all free sources, no keys required.
        </CardContent></Card>
      )}

      {snap && (
        <>
          <div className="flex flex-wrap gap-2">
            {regimeKeys.map((k) => <RegimeBadge key={k} name={k} value={regimes[k]} />)}
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {ORDER.filter((k) => snap.indicators[k]).map((k) => {
              const ind = snap.indicators[k];
              return (
                <Card key={k}>
                  <CardContent className="pt-4">
                    <div className="flex items-baseline justify-between">
                      <span className="text-xs uppercase tracking-wide text-zinc-500">{ind.label}</span>
                      {ind.above_200d != null && (
                        <span className={`text-[10px] ${ind.above_200d ? "text-emerald-600" : "text-red-600"}`}>
                          {ind.above_200d ? "▲ 200d" : "▼ 200d"}
                        </span>
                      )}
                    </div>
                    <div className="text-lg font-semibold tabular-nums">
                      {ind.value >= 100 ? ind.value.toFixed(0) : ind.value.toFixed(2)}
                    </div>
                    <div className="flex gap-2 text-xs tabular-nums">
                      <span className={ind.chg_1d_pct >= 0 ? "text-emerald-600" : "text-red-600"}>
                        1d {ind.chg_1d_pct >= 0 ? "+" : ""}{ind.chg_1d_pct.toFixed(1)}%</span>
                      <span className={ind.chg_5d_pct >= 0 ? "text-emerald-600" : "text-red-600"}>
                        5d {ind.chg_5d_pct >= 0 ? "+" : ""}{ind.chg_5d_pct.toFixed(1)}%</span>
                      <span className={ind.chg_30d_pct >= 0 ? "text-emerald-600" : "text-red-600"}>
                        30d {ind.chg_30d_pct >= 0 ? "+" : ""}{ind.chg_30d_pct.toFixed(1)}%</span>
                    </div>
                    <Sparkline values={ind.sparkline} />
                  </CardContent>
                </Card>
              );
            })}
          </div>

          <div className="grid lg:grid-cols-3 gap-6">
            <Card>
              <CardHeader><CardTitle className="text-base">War-risk meter</CardTitle></CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div>
                  <div className="flex justify-between text-xs text-zinc-500">
                    <span>GPR index (Caldara-Iacoviello)</span>
                    <span>{snap.gpr.available ? `${snap.gpr.value?.toFixed(0)} · ${snap.gpr.percentile_1y?.toFixed(0)}th pct (1y)` : "unavailable"}</span>
                  </div>
                  {snap.gpr.available && (
                    <div className="mt-1 h-3 rounded bg-zinc-100">
                      <div className={`h-3 rounded ${(snap.gpr.percentile_1y ?? 0) >= 85 ? "bg-red-500" : (snap.gpr.percentile_1y ?? 0) >= 70 ? "bg-amber-500" : "bg-emerald-500"}`}
                        style={{ width: `${snap.gpr.percentile_1y ?? 0}%` }} />
                    </div>
                  )}
                </div>
                <div>
                  <div className="flex justify-between text-xs text-zinc-500">
                    <span>War-news coverage (GDELT, vs 3m baseline)</span>
                    <span>{snap.war.available ? `${snap.war.coverage_pct?.toFixed(2)}% · z=${snap.war.z_score?.toFixed(2)}` : "unavailable"}</span>
                  </div>
                  {snap.war.available && (
                    <div className="mt-1 h-3 rounded bg-zinc-100">
                      <div className={`h-3 rounded ${(snap.war.z_score ?? 0) >= 1.5 ? "bg-red-500" : (snap.war.z_score ?? 0) >= 0.75 ? "bg-amber-500" : "bg-emerald-500"}`}
                        style={{ width: `${Math.min(100, Math.max(4, ((snap.war.z_score ?? 0) + 2) / 4 * 100))}%` }} />
                    </div>
                  )}
                </div>
                {Object.keys(snap.fred).length > 0 && (
                  <div className="pt-2 border-t text-xs space-y-1">
                    {Object.entries(snap.fred).map(([k, v]) => (
                      <div key={k} className="flex justify-between">
                        <span className="text-zinc-500">{k}</span><span className="tabular-nums">{v}</span>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader><CardTitle className="text-base">Conflict & sanctions headlines</CardTitle></CardHeader>
              <CardContent>
                <ul className="space-y-2 text-sm">
                  {(snap.war.headlines ?? []).slice(0, 8).map((h, i) => (
                    <li key={i}>
                      <a href={h.url} target="_blank" rel="noreferrer" className="hover:underline">{h.title}</a>
                      <span className="text-xs text-zinc-400"> — {h.source}</span>
                    </li>
                  ))}
                  {(!snap.war.headlines || snap.war.headlines.length === 0) &&
                    <li className="text-zinc-400">No headlines fetched</li>}
                </ul>
              </CardContent>
            </Card>

            <Card>
              <CardHeader><CardTitle className="text-base">Macro agent briefing</CardTitle></CardHeader>
              <CardContent>
                {report ? (
                  <>
                    <div className="text-sm whitespace-pre-wrap">{report.narrative}</div>
                    <p className="mt-2 text-xs text-zinc-400">{new Date(report.created_at).toLocaleString()}</p>
                  </>
                ) : (
                  <p className="text-sm text-zinc-400">
                    {llm ? "No briefing yet — run the macro agent." : "Requires OPENAI_API_KEY on the backend."}
                  </p>
                )}
              </CardContent>
            </Card>
          </div>

          <p className="text-xs text-zinc-400">
            Regime flags are computed deterministically (documented thresholds) and feed the risk gateway:
            in risk-off / high-volatility / high-war-risk regimes, new position sizes are damped ×0.7.
            The agent only narrates — it never changes the flags.
          </p>
        </>
      )}
    </div>
  );
}
