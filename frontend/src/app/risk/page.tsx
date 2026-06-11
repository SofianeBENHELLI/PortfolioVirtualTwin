"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type Summary = {
  drawdown_pct: number; max_drawdown_pct: number; volatility_pct: number;
  n_positions: number; top_position_weight_pct: number;
  sector_weights: Record<string, number>; open_orders: number; equity: number;
};
type Alert = {
  id: number; level: string; kind: string; title: string; body: string;
  acknowledged: boolean; created_at: string;
};

const LEVEL_COLOR: Record<string, string> = {
  critical: "bg-red-600", warning: "bg-amber-500", info: "bg-blue-600",
};

export default function RiskCockpit() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const portfolios = await api<{ id: number }[]>("/api/portfolios");
      if (portfolios.length > 0)
        setSummary(await api(`/api/portfolios/${portfolios[0].id}/summary`));
      setAlerts(await api("/api/alerts"));
    } catch (e) { setError(e instanceof Error ? e.message : "load failed"); }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 20_000);
    return () => clearInterval(t);
  }, [load]);

  async function ack(id: number) {
    await api(`/api/alerts/${id}/ack`, { method: "POST" });
    load();
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Risk Cockpit</h1>
      {error && <p className="text-sm text-red-600">{error}</p>}

      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <Gauge label="Current drawdown" value={summary.drawdown_pct} suffix="%" warnAt={10} />
          <Gauge label="Max drawdown" value={summary.max_drawdown_pct} suffix="%" warnAt={15} />
          <Gauge label="Volatility (ann.)" value={summary.volatility_pct} suffix="%" warnAt={25} />
          <Gauge label="Top position weight" value={summary.top_position_weight_pct} suffix="%" warnAt={15} />
          <Gauge label="Open orders" value={summary.open_orders} warnAt={5} />
        </div>
      )}

      {summary && (
        <Card>
          <CardHeader><CardTitle className="text-base">Sector concentration</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            {Object.entries(summary.sector_weights).sort((a, b) => b[1] - a[1]).map(([sector, w]) => (
              <div key={sector} className="flex items-center gap-3 text-sm">
                <span className="w-40 truncate">{sector}</span>
                <div className="flex-1 bg-zinc-100 rounded h-3">
                  <div className={`h-3 rounded ${w > 30 ? "bg-red-500" : "bg-blue-500"}`}
                    style={{ width: `${Math.min(100, w)}%` }} />
                </div>
                <span className="w-14 text-right">{w.toFixed(1)}%</span>
              </div>
            ))}
            {Object.keys(summary.sector_weights).length === 0 &&
              <p className="text-sm text-zinc-400">No positions</p>}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader><CardTitle className="text-base">Alerts</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          {alerts.map((a) => (
            <div key={a.id} className={`border rounded-lg p-3 ${a.acknowledged ? "opacity-50" : ""}`}>
              <div className="flex items-center gap-2">
                <Badge className={LEVEL_COLOR[a.level] ?? "bg-zinc-500"}>{a.level}</Badge>
                <Badge variant="outline">{a.kind}</Badge>
                <span className="font-medium text-sm">{a.title}</span>
                <span className="ml-auto text-xs text-zinc-400">{new Date(a.created_at).toLocaleString()}</span>
                {!a.acknowledged && (
                  <Button size="sm" variant="outline" onClick={() => ack(a.id)}>Acknowledge</Button>
                )}
              </div>
              {a.body && <p className="text-sm text-zinc-600 mt-1">{a.body}</p>}
            </div>
          ))}
          {alerts.length === 0 && <p className="text-sm text-zinc-400">No alerts — the monitor raises risk-limit, exit-rule and concentration alerts here.</p>}
        </CardContent>
      </Card>
    </div>
  );
}

function Gauge({ label, value, suffix = "", warnAt }: { label: string; value: number; suffix?: string; warnAt: number }) {
  const hot = value >= warnAt;
  return (
    <Card className={hot ? "border-red-400" : ""}>
      <CardContent className="pt-4">
        <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
        <div className={`text-xl font-semibold ${hot ? "text-red-600" : ""}`}>
          {value.toFixed(suffix ? 1 : 0)}{suffix}
        </div>
      </CardContent>
    </Card>
  );
}
