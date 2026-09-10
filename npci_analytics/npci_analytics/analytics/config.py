import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "npci_upi.db")

# Which column represents "decline rate" for forecasting/change-point detection.
# TD% (Technical Decline) reflects genuine technical/infrastructure failures on
# the bank's side - this project's actual focus, as distinct from BD% which can
# include customer-side declines (e.g. insufficient balance) and fraud-adjacent
# business declines. Change this to 'bd_pct' if you want to analyze that instead.
TARGET_METRIC = "td_pct"

# Minimum number of historical months required before we attempt to forecast
# or detect change-points for a bank - too few points makes both meaningless.
MIN_MONTHS_REQUIRED = 6

# How many of the most recent months to hold out for backtesting forecast accuracy
BACKTEST_HOLDOUT_MONTHS = 3
