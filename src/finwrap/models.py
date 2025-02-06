from enum import Enum
from pathlib import Path
from typing import Annotated, Callable, TypeAlias

import polars as pl
import yaml
from pydantic import BaseModel, Field
from pydantic.functional_serializers import PlainSerializer

from .currency import Currency

_Path = Annotated[Path, PlainSerializer(lambda x: str(x.resolve()), return_type=str)]

FilePath: TypeAlias = _Path | list[_Path] | str | list[str]


class FileType(Enum):
    CSV = ".csv"
    PARQUET = ".parquet"
    EXCEL = ".xlsx"
    EXCEL_OLD = ".xls"


FILE_READERS: dict[FileType, Callable[..., pl.LazyFrame]] = {
    FileType.CSV: pl.scan_csv,
    FileType.PARQUET: pl.scan_parquet,
    FileType.EXCEL: lambda x: pl.read_excel(x).lazy(),
    FileType.EXCEL_OLD: lambda x: pl.read_excel(x).lazy(),
}


def _read_data(
    file_path: FilePath,
) -> pl.LazyFrame:
    """return a LazyFrame based on the appropriate suffix"""
    if isinstance(file_path, (list)):
        suffix = Path(file_path[0]).suffix
        file_type = next((ft for ft in FileType if ft.value == suffix), None)

        if file_type is None:
            raise ValueError(
                f"Unsupported file type for {file_path} with suffix {suffix}"
            )

        if not all(Path(p).suffix == suffix for p in file_path):
            raise ValueError("All files must have the same suffix")

        return FILE_READERS[file_type](file_path)

    elif isinstance(file_path, (str, Path)):
        return _read_data([str(file_path)])


class Account(BaseModel, arbitrary_types_allowed=True):
    file_path: FilePath | list[FilePath]
    name: str
    date_col: str
    transaction_col: str
    amount_col: str
    date_col_format: str | None = None
    currency: Currency | None = None
    fees_col: str | None = None
    transaction_col_cleaning_regex: str | None = None

    # Internals
    data: pl.LazyFrame = Field(
        default_factory=lambda data: _read_data(data["file_path"]),
        repr=False,
        exclude=True,
    )
    data_schema: pl.Schema = Field(
        default_factory=lambda data: data["data"].collect_schema(),
        repr=False,
        exclude=True,
    )

    def save(self, fname: str):
        with open(fname, "w") as f:
            f.write(yaml.dump(self.model_dump()))

    @classmethod
    def load(cls, fname: str):
        with open(fname) as f:
            return cls.model_validate(yaml.safe_load(f.read()))

    @property
    def amount(self) -> pl.Expr:
        schema = self.data_schema[self.amount_col]
        if isinstance(schema, pl.String):
            amount_val = pl.col(self.amount_col).str.replace(",", "").cast(pl.Float64)
        else:
            amount_val = pl.col(self.amount_col)

        if self.fees_col:
            amount_val -= pl.col(self.fees_col)

        if self.currency is not None:
            amount_val *= self.currency.rate(self.date)

        return amount_val

    @property
    def date(self) -> pl.Expr:
        schema = self.data_schema[self.date_col]
        if isinstance(schema, pl.String):
            return (
                pl.col(self.date_col)
                .str.to_datetime(self.date_col_format)
                .cast(pl.Datetime("us"))
            )
        else:
            return pl.col(self.date_col).cast(pl.Datetime("us"))

    @property
    def transaction(self) -> pl.Expr:
        col = pl.col(self.transaction_col)
        if self.transaction_col_cleaning_regex:
            col = col.str.replace_all(self.transaction_col_cleaning_regex, "")
        return col.str.strip_chars()

    def get_data(self) -> pl.LazyFrame:
        for col in [self.date_col, self.transaction_col, self.transaction_col]:
            assert (
                col in self.data_schema.names()
            ), f"{col} is not in data columns: {self.data.columns}. col={col}; cols: {self.data.columns}"
        return (
            self.data.sort(self.date_col)
            .select(
                account_name=pl.lit(self.name),
                date=self.date,
                transaction=self.transaction,
                amount=self.amount,
            )
            .unique()
        )


class AccountCollection(BaseModel, arbitrary_types_allowed=True):
    accounts: list[Account]

    def save(self, fname: str):
        with open(fname, "w") as f:
            f.write(yaml.dump(self.model_dump()))

    @classmethod
    def load(cls, fname: str):
        with open(fname) as f:
            return cls.model_validate(yaml.safe_load(f.read()))

    def get_data(self) -> pl.LazyFrame:
        return pl.concat(
            [d.get_data().lazy() for d in self.accounts],
            how="vertical",
        )
