"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

type Me = {
  user_id: number; email: string; has_personal_key: boolean;
  personal_key_hint: string | null; llm_available: boolean; key_source: string;
};

export default function SettingsPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [keyInput, setKeyInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");

  const load = useCallback(async () => {
    try { setMe(await api<Me>("/api/auth/me")); }
    catch (e) { setError(e instanceof Error ? e.message : "load failed"); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function save(clear = false) {
    setBusy(true); setError(""); setInfo("");
    try {
      await api("/api/auth/me/openai-key", {
        method: "PUT",
        body: JSON.stringify({ api_key: clear ? "" : keyInput }),
      });
      setKeyInput("");
      setInfo(clear ? "Personal key removed." : "Key saved (encrypted). Your agents now run on your own OpenAI account.");
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
    finally { setBusy(false); }
  }

  return (
    <div className="max-w-2xl space-y-6">
      <h1 className="text-2xl font-bold">Settings</h1>

      <Card>
        <CardHeader><CardTitle className="text-base">Account</CardTitle></CardHeader>
        <CardContent className="text-sm text-zinc-600">
          Signed in as <span className="font-medium">{me?.email ?? "…"}</span>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            Your OpenAI API key
            {me?.llm_available
              ? <Badge className="bg-emerald-600">agents active · {me.key_source} key</Badge>
              : <Badge className="bg-zinc-400">agents disabled</Badge>}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-zinc-600">
            This is a shared project: <b>each user brings their own OpenAI key</b>, so AI usage
            (research, Bull &amp; Bear, briefings, strategy builder) is billed to whoever runs it.
            Your key is stored encrypted on the server and is never shown again in full.
            Get one at <a className="text-blue-600 hover:underline" href="https://platform.openai.com/api-keys"
              target="_blank" rel="noreferrer">platform.openai.com/api-keys</a>.
          </p>
          {me?.has_personal_key && (
            <p className="text-sm">
              Current personal key: <code className="rounded bg-zinc-100 px-1.5 py-0.5">{me.personal_key_hint}</code>
            </p>
          )}
          {!me?.has_personal_key && me?.key_source === "shared" && (
            <p className="text-sm text-amber-700 bg-amber-50 rounded p-2">
              You&apos;re currently using the server&apos;s shared key — add your own so your usage is billed to you.
            </p>
          )}
          <div className="flex gap-2">
            <Input type="password" value={keyInput} onChange={(e) => setKeyInput(e.target.value)}
              placeholder="sk-…" autoComplete="off" />
            <Button onClick={() => save(false)} disabled={busy || keyInput.trim().length < 20}>
              {me?.has_personal_key ? "Replace key" : "Save key"}
            </Button>
            {me?.has_personal_key && (
              <Button variant="outline" onClick={() => save(true)} disabled={busy}>Remove</Button>
            )}
          </div>
          {info && <p className="text-sm text-emerald-700">{info}</p>}
          {error && <p className="text-sm text-red-600">{error}</p>}
          <p className="text-xs text-zinc-500">
            Cost control: every agent run records its token usage (Research Desk → Agent runs).
            Typical run: research ≈ 1-3¢ per stock, briefing ≈ 1¢.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
