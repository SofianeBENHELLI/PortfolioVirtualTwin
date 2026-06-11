"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

type Version = { id: number; version: number; comment: string; created_at: string };
type StrategyDetail = {
  id: number; name: string; active_version_id: number;
  active_version: { version: number; yaml: string } | null;
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

  const load = useCallback(async () => {
    try {
      const d = await api<StrategyDetail>(`/api/strategies/${id}`);
      setDetail(d);
      setYamlText(d.active_version?.yaml ?? "");
    } catch (e) { setError(e instanceof Error ? e.message : "load failed"); }
  }, [id]);
  useEffect(() => { load(); }, [load]);

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
