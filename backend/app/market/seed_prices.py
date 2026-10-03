"""Starting prices and per-ticker GBM parameters for the simulator."""

SEED_PRICES: dict[str, float] = {
    "AAPL": 190.00,
    "GOOGL": 175.00,
    "MSFT": 420.00,
    "AMZN": 185.00,
    "TSLA": 250.00,
    "NVDA": 800.00,
    "META": 500.00,
    "JPM": 195.00,
    "V": 280.00,
    "NFLX": 600.00,
}

DEFAULT_WATCHLIST: list[str] = list(SEED_PRICES)

# sigma: annualized volatility. mu: annualized drift.
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL": {"sigma": 0.22, "mu": 0.05},
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT": {"sigma": 0.20, "mu": 0.05},
    "AMZN": {"sigma": 0.28, "mu": 0.05},
    "TSLA": {"sigma": 0.50, "mu": 0.03},
    "NVDA": {"sigma": 0.40, "mu": 0.08},
    "META": {"sigma": 0.30, "mu": 0.05},
    "JPM": {"sigma": 0.18, "mu": 0.04},
    "V": {"sigma": 0.17, "mu": 0.04},
    "NFLX": {"sigma": 0.35, "mu": 0.05},
}

DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}

SECTORS: dict[str, str] = {
    "AAPL": "tech",
    "GOOGL": "tech",
    "MSFT": "tech",
    "AMZN": "tech",
    "META": "tech",
    "NVDA": "tech",
    "NFLX": "tech",
    "TSLA": "tsla",  # trades on its own story
    "JPM": "finance",
    "V": "finance",
}

INTRA_TECH_CORR = 0.6
INTRA_FINANCE_CORR = 0.5
TSLA_CORR = 0.3
CROSS_SECTOR_CORR = 0.3  # also used for any ticker not listed in SECTORS

# Random starting range for a ticker added at runtime that is not in SEED_PRICES.
UNKNOWN_PRICE_RANGE = (50.0, 300.0)
