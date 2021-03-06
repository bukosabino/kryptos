import json
import logbook
import talib as ta
from typing import List, Set
from talib import abstract as ab
import ccxt

from kryptos import logger_group
from kryptos.strategy import Strategy
from kryptos.settings import REDIS_HOST, REDIS_PORT


log = logbook.Logger("WorkerJobs")
logger_group.add_logger(log)
log.warn(f"Using Redis connection {REDIS_HOST}:{REDIS_PORT}")


def run_strat(
    strat_json,
    strat_id,
    user_id=None,
    telegram_id=None,
    live=False,
    simulate_orders=True,
):
    log.info(f"Worker received job for strat {strat_id}")
    strat_dict = json.loads(strat_json)
    strat = Strategy.from_dict(strat_dict)
    strat.id = strat_id
    strat.telegram_id = telegram_id

    strat.run(
        viz=False,
        live=live,
        simulate_orders=simulate_orders,
        user_id=user_id,
        as_job=True,
    )
    result_df = strat.quant_results
    if result_df is None:
        log.warning("No results from strategy")
        return

    return result_df.to_json()


## TA-LIB utils ##
def indicator_group_name_selectors() -> [(str, str)]:
    """Returns list of select options of indicator group names"""
    selectors = []
    for k in ta.get_function_groups().keys():
        selectors.append((k, k))
    return selectors


def all_indicator_selectors() -> [(str, str)]:
    """Returns the entire list of possible indicator abbreviation select options"""
    selectors = []
    for i in ta.get_functions():
        selectors.append((i, i))
    return selectors


def _get_indicator_params(indicator_abbrev):
    func = getattr(ab, indicator_abbrev)
    return func.parameters


def get_indicators_by_group(group: str) -> [(str, str)]:
    """Returns list of select options containing abbreviations of the groups indicators"""
    indicator_selects = []
    group_indicators = ta.get_function_groups()[group]
    for i in range(len(group_indicators)):
        abbrev = group_indicators[i]
        func = getattr(ab, abbrev)
        name = func.info["display_name"]
        indicator_selects.append((abbrev, abbrev))
    return indicator_selects


def get_exchange_asset_pairs(exchange: str) -> [str]:
    log.debug(f"Fetching {exchange} markets with ccxt")
    exchange_class = getattr(ccxt, exchange)
    markets = exchange_class().load_markets()
    symbols = []
    for pair in markets:
        s = pair.replace("/", "_").lower()
        symbols.append(s)
    return symbols


def get_exchange_quote_currencies(exchange: str) -> Set[str]:
    symbols = get_exchange_asset_pairs(exchange)
    quotes = set()
    for s in symbols:
        _, quote = s.split("_")
        quotes.add(quote)
    return quotes


def get_available_base_currencies(exchange: str, quote_currency: str) -> Set[str]:
    symbols = get_exchange_asset_pairs(exchange)
    base_currencies = set()
    for s in [s for s in symbols if quote_currency in s]:
        base, quote = s.split("_")
        if quote == quote_currency:
            base_currencies.add(base)

    return base_currencies
