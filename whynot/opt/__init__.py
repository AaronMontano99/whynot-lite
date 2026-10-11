"""The options engine. Agents decide whether there is a trade; this package decides
which contract, if any, qualifies, and what a fill would have cost.

Three kinds of number appear here and are never mixed up:
  REAL     traded prices, volume and trade counts from the provider;
  DERIVED  implied volatility and Greeks, computed from a real traded price;
  ASSUMED  the cost of crossing the spread, because no historical bid/ask exists."""
