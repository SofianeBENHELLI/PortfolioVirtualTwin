"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

type Entry = {
  id: number; actor: string; action: string; entity: string; entity_id: string;
  payload: Record<string, unknown>; created_at: string;
};

const ACTOR_COLOR: Record<string, string> = {
  user: "bg-blue-600", agent: "bg-purple-600", system: "bg-zinc-600",
};

export default function AuditLog() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try { setEntries(await api("/api/audit?limit=200")); }
    catch (e) { setError(e instanceof Error ? e.message : "load failed"); }
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 20_000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Audit Log
        <span className="ml-3 text-sm font-normal text-zinc-500">append-only · every state change</span>
      </h1>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <Card><CardContent className="pt-4">
        <Table>
          <TableHeader><TableRow>
            <TableHead>Actor</TableHead><TableHead>Action</TableHead><TableHead>Entity</TableHead>
            <TableHead>Payload</TableHead><TableHead>Time</TableHead>
          </TableRow></TableHeader>
          <TableBody>
            {entries.map((e) => (
              <TableRow key={e.id}>
                <TableCell><Badge className={ACTOR_COLOR[e.actor] ?? "bg-zinc-500"}>{e.actor}</Badge></TableCell>
                <TableCell className="font-mono text-xs">{e.action}</TableCell>
                <TableCell className="text-xs">{e.entity}{e.entity_id ? ` #${e.entity_id}` : ""}</TableCell>
                <TableCell className="text-xs font-mono max-w-md truncate">{JSON.stringify(e.payload)}</TableCell>
                <TableCell className="text-xs text-zinc-500 whitespace-nowrap">{new Date(e.created_at).toLocaleString()}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent></Card>
    </div>
  );
}
