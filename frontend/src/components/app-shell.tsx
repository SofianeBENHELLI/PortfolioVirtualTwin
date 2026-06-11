"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, fmtMoney, fmtPct, getToken, pnlColor, setToken } from "@/lib/api";
import { Badge } from "@/components/ui/badge";

const NAV: { section: string; items: { href: string; label: string }[] }[] = [
  {
    section: "My desk",
    items: [
      { href: "/", label: "Portfolio" },
      { href: "/stocks", label: "My Stocks" },
      { href: "/risk", label: "Risks" },
    ],
  },
  {
    section: "Decide",
    items: [
      { href: "/trading", label: "Trading Console" },
      { href: "/research", label: "Research Desk" },
      { href: "/backtests", label: "Backtest Lab" },
    ],
  },
  {
    section: "Setup",
    items: [
      { href: "/strategies", label: "My Strategy" },
      { href: "/audit", label: "Audit Log" },
    ],
  },
];

type Summary = {
  equity: number; daily_pnl: number; daily_pnl_pct: number; mode: string;
  drawdown_pct: number; open_orders: number; name: string;
};

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [realEquity, setRealEquity] = useState<number | null>(null);
  const [alertCount, setAlertCount] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const portfolios = await api<{ id: number; kind: string }[]>("/api/portfolios");
      const paper = portfolios.find((p) => p.kind === "paper");
      if (paper) {
        setSummary(await api<Summary>(`/api/portfolios/${paper.id}/summary`));
      }
      const real = portfolios.find((p) => p.kind === "real_tracked");
      if (real) {
        const rs = await api<Summary>(`/api/portfolios/${real.id}/summary`);
        setRealEquity(rs.equity);
      } else setRealEquity(null);
      const alerts = await api<{ acknowledged: boolean }[]>("/api/alerts");
      setAlertCount(alerts.filter((a) => !a.acknowledged).length);
    } catch {}
  }, []);

  useEffect(() => {
    if (!getToken()) {
      setAuthed(false);
      if (pathname !== "/login") router.replace("/login");
      return;
    }
    setAuthed(true);
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  }, [pathname, router, refresh]);

  if (pathname === "/login") return <>{children}</>;
  if (authed === false) return null;

  return (
    <div className="flex min-h-screen">
      <aside className="w-56 shrink-0 border-r bg-zinc-950 text-zinc-100 flex flex-col">
        <div className="px-4 py-5 border-b border-zinc-800">
          <div className="font-bold tracking-tight">PortfolioVirtualTwin</div>
          <div className="text-xs text-zinc-400">Strategy Twin · Investment OS</div>
        </div>
        <nav className="flex-1 py-3">
          {NAV.map((group) => (
            <div key={group.section} className="mb-3">
              <div className="px-4 pb-1 text-[10px] font-semibold uppercase tracking-widest text-zinc-500">
                {group.section}
              </div>
              {group.items.map((item) => (
                <Link key={item.href} href={item.href}
                  className={`block px-4 py-2 text-sm hover:bg-zinc-800 ${
                    pathname === item.href ? "bg-zinc-800 font-medium border-l-2 border-amber-400" : "text-zinc-300"}`}>
                  {item.label}
                  {item.href === "/risk" && alertCount > 0 && (
                    <span className="ml-2 rounded-full bg-red-600 px-1.5 text-xs">{alertCount}</span>
                  )}
                </Link>
              ))}
            </div>
          ))}
        </nav>
        <button onClick={() => { setToken(null); router.push("/login"); }}
          className="px-4 py-3 text-left text-sm text-zinc-400 hover:text-zinc-100 border-t border-zinc-800">
          Sign out
        </button>
      </aside>
      <div className="flex-1 flex flex-col min-w-0">
        <header className="flex items-center gap-4 border-b bg-amber-50 px-6 py-2 text-sm">
          <Badge className="bg-amber-500 text-black hover:bg-amber-500">PAPER TRADING</Badge>
          {summary && (
            <>
              <span className="font-medium">{fmtMoney(summary.equity)}</span>
              <span className={pnlColor(summary.daily_pnl)}>
                {fmtMoney(summary.daily_pnl)} ({fmtPct(summary.daily_pnl_pct)}) today
              </span>
              <span className="text-zinc-500">drawdown {summary.drawdown_pct.toFixed(1)}%</span>
              <span className="text-zinc-500">{summary.open_orders} open orders</span>
            </>
          )}
          {realEquity != null && (
            <span className="flex items-center gap-1.5 text-zinc-600">
              <Badge className="bg-emerald-600 hover:bg-emerald-600">REAL</Badge>
              {fmtMoney(realEquity)} <span className="text-xs text-zinc-400">(tracked)</span>
            </span>
          )}
          <span className="ml-auto text-zinc-400 text-xs">No real money. Agents propose — you approve.</span>
        </header>
        <main className="flex-1 p-6 bg-zinc-50">{children}</main>
      </div>
    </div>
  );
}
