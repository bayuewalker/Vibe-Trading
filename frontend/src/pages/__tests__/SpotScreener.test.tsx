import { render, screen, waitFor } from "@testing-library/react";
import { SpotScreener } from "../SpotScreener";

vi.mock("@/lib/apiAuth", () => ({
  authHeaders: () => ({ Authorization: "Bearer test-token" }),
}));

it("renders autonomous research-only results with manual TradingView chart links", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      enabled: true,
      running: true,
      interval_seconds: 900,
      source: "binance_spot_public_api",
      trading_enabled: false,
      last_error: null,
      snapshot: {
        as_of: new Date().toISOString(),
        source: "binance_spot_public_api",
        mode: "research_only",
        screened: 12,
        qualified: 1,
        candidates: [{
          symbol: "BTCUSDT",
          chart_url: "https://www.tradingview.com/chart/?symbol=BINANCE%3ABTCUSDT",
          score: 80,
          classification: "watchlist",
          price_usdt: 94000,
          change_24h_pct: 2.1,
          quote_volume_24h_usdt: 200000000,
          rsi14_1h: 59.2,
          relative_volume_1h: 1.8,
          trend_1h: true,
          trend_4h: true,
        }],
      },
    }),
  });
  vi.stubGlobal("fetch", fetchMock);
  const { unmount } = render(<SpotScreener />);

  expect(await screen.findByText("BTC/USDT")).toBeInTheDocument();
  expect(screen.getByText(/Live orders: OFF/)).toBeVisible();
  expect(screen.getByText(/TradingView does not supply this data/)).toBeVisible();
  expect(screen.getByRole("link", { name: "TradingView chart" })).toHaveAttribute(
    "href", "https://www.tradingview.com/chart/?symbol=BINANCE%3ABTCUSDT",
  );
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    "/api/spot-screener",
    expect.objectContaining({ headers: { Authorization: "Bearer test-token" } }),
  ));
  unmount();
  vi.unstubAllGlobals();
});
