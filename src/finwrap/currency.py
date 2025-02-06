import logging
from datetime import date, datetime
from functools import cache
from typing import Literal

import polars as pl
import requests
from pydantic import BaseModel

BASE_URL = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{date}/v1/currencies/{currency}.json"


@cache
def get_currency_rate(
    from_currency: str,
    to_currency: str,
    date_input: date | datetime | Literal["latest"],
    default_rate: float | None = None,
) -> float | None:
    logging.getLogger(__name__).info(
        f"fetching {from_currency}/{to_currency} for {date_input!r}"
    )
    from_currency = from_currency.lower()
    to_currency = to_currency.lower()
    date_str = (
        date_input.strftime("%F")
        if isinstance(date_input, (date, datetime))
        else date_input
    )
    url = BASE_URL.format(currency=from_currency, date=date_str)
    response = requests.get(url)
    if response.status_code % 200 == 0:
        rate = response.json().get(from_currency, {}).get(to_currency)
        if rate is None and default_rate is None:
            raise ValueError(
                f"Unable to fetch exchange rate for {from_currency.upper()}/{to_currency.upper()} and no default_rate was specified."
            )
        elif rate is not None:
            return rate
    return default_rate


def get_currency_rate_batches(
    from_currency: list[str],
    to_currency: str,
    date_input: list[date | datetime | Literal["latest"]],
    default_rate: float | None = None,
) -> pl.Series:
    return pl.Series(
        "rate",
        values=(
            get_currency_rate(c, to_currency, d)
            for c, d in zip(from_currency, date_input)
        ),
        dtype=pl.Float64,
    )


class Currency(BaseModel):
    currency_col: str
    convert_to: str
    default_rate: float | None = None
    strategy: Literal["dynamic", "latest"] = "latest"

    def rate(self, date_expr: pl.Expr) -> pl.Expr:
        currency_col = pl.col(self.currency_col).alias("currency_col")
        date_col = date_expr.alias("date_col")
        if self.strategy == "dynamic":
            return (
                pl.when(currency_col != self.convert_to)
                .then(
                    pl.struct(currency_col, date_col).map_batches(
                        lambda x: get_currency_rate_batches(
                            x.struct.field("currency_col"),  # type: ignore
                            self.convert_to,
                            x.struct.field("date_col"),  # type: ignore
                            self.default_rate,
                        ),
                        return_dtype=pl.Float64,
                    )
                )
                .otherwise(pl.lit(1.0))
            )
        elif self.strategy == "latest":
            return (
                pl.when(currency_col != self.convert_to)
                .then(
                    currency_col.map_elements(
                        lambda x: get_currency_rate(
                            x,  # type: ignore
                            self.convert_to,
                            "latest",  # type: ignore
                            self.default_rate,
                        ),
                        return_dtype=pl.Float64,
                    )
                )
                .otherwise(pl.lit(1.0))
            )
        else:
            raise ValueError(f"Strategy {self.strategy} is not valid.")
