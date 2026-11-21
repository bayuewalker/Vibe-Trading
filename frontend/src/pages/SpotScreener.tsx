import { useCallback, useEffect, useState } from "react";
import { authHeaders } from "@/lib/apiAuth";

type Candidate = {
  symbol: string;
  chart_url: string;
  score: number;
  classification: "watchlist" | "neutral";
  price_usdt: number;
  change_24h_pct: number | null;
  quote_volume_24h_usdt: number;
  rsi14_1h: number;
  relative_volume_1h: number;
  trend_1h: boolean;
  trend_4h: boolean;
};
type Snapshot = {
  as_of: string;
  source: string;
  mode: string;
  screened: number;
  qualified: number;
  candidates: Candidate[];
};
type Status = {
  enabled: boolean;
  running: boolean;
  interval_seconds: number;
  source: string;
  trading_enabled: boolean;
  last_error: string | null;
  snapshot: Snapshot | null;
};

const formatNumber = (number: number, maxDigits = 2) =>
  new Intl.NumberFormat("en-US", { maximumFractionDigits: maxDigits }).format(number);

export function SpotScreener() {
  const [data, setData] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await fetch("/api/spot-screener", { headers: authHeaders(), signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}: unable to load spot screening`);
      const body = (await response.json()) as Status;
      if (!signal?.aborted) {
        setData(body);
        setError(null);
      }
    } catch (reason) {
      if (!signal?.aborted) setError(reason instanceof Error ? reason.message : "Unable to load screening data");
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    const timer = setInterval(() => void load(controller.signal), 30_000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [load]);

  const snapshot = data?.snapshot;
  const stale = snapshot ? Date.now() - new Date(snapshot.as_of).getTime() > Math.max((data?.interval_seconds ?? 900) * 2000, 30 * 60_000) : false;
  return (
    <main className="mx-auto max-w-6xl space-y-6 p-4 md:p-8">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold">Crypto Spot Screener</h1>
        <p className="text-sm text-muted-foreground">Automated Binance Spot / USDT research screening. TradingView links are for chart review; TradingView does not supply this data.</p>
      </header>
      <div className="flex flex-wrap gap-2 text-sm" aria-live="polite">
        <span className="rounded border px-3 py-1">Auto scan: {data?.running ? "Running" : data?.enabled ? "Not running" : "Disabled"}</span>
        <span className="rounded border px-3 py-1">Interval: {Math.round((data?.interval_seconds ?? 900) / 60)} min</span>
        <span className="rounded border px-3 py-1">Mode: Research only</span>
        <span className="rounded border px-3 py-1">Live orders: OFF</span>
      </div>
      {error && <p role="alert" className="rounded border border-destructive p-3 text-sm text-destructive">{error}. Check API authentication in Settings.</p>}
      {data?.last_error && <p role="alert" className="rounded border border-amber-500/40 p-3 text-sm">Latest scan failed: {data.last_error}. Previous results are retained and may be stale.</p>}
      {stale && <p role="status" className="text-sm text-amber-600">Last successful snapshot is stale. Do not treat these quotes as live.</p>}
      {snapshot ? (
        <>
          <p className="text-sm text-muted-foreground">As of {new Date(snapshot.as_of).toLocaleString("en-US")} · {snapshot.screened} pairs screened · {snapshot.qualified} qualified · 1H + 4H closed candles.</p>
          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full min-w-[780px] text-left text-sm">
              <thead className="bg-muted/60 text-muted-foreground"><tr>
                <th className="p-3">Pair</th><th className="p-3">Score / 100</th><th className="p-3">1H / 4H</th><th className="p-3">Price (USDT)</th><th className="p-3">24H</th><th className="p-3">RSI 1H</th><th className="p-3">Rel. volume</th><th className="p-3">Review</th>
              </tr></thead>
              <tbody>{snapshot.candidates.map((item) => <tr key={item.symbol} className="border-t">
                <td className="p-3 font-medium">{item.symbol.replace(/USDT$/, "/USDT")}</td>
                <td className="p-3">{item.score} <span className="text-muted-foreground">({item.classification})</span></td>
                <td className="p-3">{item.trend_1h ? "↑" : "–"} / {item.trend_4h ? "↑" : "–"}</td>
                <td className="p-3">{formatNumber(item.price_usdt, 8)}</td>
                <td className="p-3">{item.change_24h_pct === null ? "—" : `${formatNumber(item.change_24h_pct)}%`}</td>
                <td className="p-3">{formatNumber(item.rsi14_1h, 1)}</td>
                <td className="p-3">{formatNumber(item.relative_volume_1h)}x</td>
                <td className="p-3"><a className="text-primary underline" href={item.chart_url} target="_blank" rel="noopener noreferrer">TradingView chart</a></td>
              </tr>)}</tbody>
            </table>
          </div>
        </>
      ) : (
        <p className="rounded border p-4 text-sm text-muted-foreground">No completed scan yet. Enable VIBE_SPOT_SCREEN_ENABLED=1 on the Fly Machine and verify Binance public market-data access.</p>
      )}
      <p className="text-xs text-muted-foreground">Scores are deterministic technical filters, not profit probabilities or trade recommendations. No orders, DCA bots or AI model calls are triggered by screening.</p>
    </main>
  );
}
