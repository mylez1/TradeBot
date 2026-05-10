# Max shares per order. Polymarket requires ~$1 min notional; at ~0.5/share you need ≥2 shares.
STRATEGY_VERSION = "DOWN_ONLY_V2"

MAX_TRADE_SIZE = 5.0
# Minimum USD notional (price × shares) for a buy; exchange rejects smaller marketable orders.
MIN_ORDER_NOTIONAL_USD = 1.0
MAX_TRADES = 1
COOLDOWN_SECONDS = 60.0

# Only take DOWN entries when orderbook imbalance is strong enough.
# More negative imbalance => stronger DOWN signal.
MIN_IMBALANCE = -0.5
MAX_SPREAD_BPS = 300

# Only enter during the highest-quality part of the 5-minute market window.
MIN_ENTRY_AGE = 60
MAX_ENTRY_AGE = 120
