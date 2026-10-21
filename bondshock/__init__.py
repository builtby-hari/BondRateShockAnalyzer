"""Bond rate-shock analyzer: full repricing vs duration/convexity approximations."""

from .pricing import (Bond, price, macaulay_duration, modified_duration,
                      convexity, cashflow_times)
from .portfolio import (KEY_TENORS, load_portfolio, key_rate_weights,
                        run_shocks, summary, SHOCKS_BP)
from .curve import (load_par_curve, discount_factors, key_rate_dv01, scenario_pnl,
                    base_spreads, SCENARIOS)
