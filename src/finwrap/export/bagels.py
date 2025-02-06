import datetime
import logging
import subprocess
from pathlib import Path
from typing import Iterable

import polars as pl
import typer
from sqlalchemy import (
    Connection,
    MetaData,
    Table,
    create_engine,
    func,
    insert,
    select,
)

from finwrap.models import Account, AccountCollection

METADATA = MetaData()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class BinaryNotFoundError(Exception): ...


def locate_database() -> Path:
    try:
        logger.info("Attempting to locate database")
        res = str(
            subprocess.run(
                ["bagels", "locate", "database"], capture_output=True
            )
            .stdout.splitlines()[-1]
            .strip()
            .decode("utf-8")
        )
        logger.debug(f"Database located at: {res}")
        return Path(res)

    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.error("Failed to locate bagels binary")
        raise BinaryNotFoundError(
            "bagels not found. Try to check if it was installed correcly"
        ) from e


def get_max_id(table_name: str, conn: Connection) -> int:
    tb = Table(table_name, METADATA, autoload_with=conn)
    return conn.execute(select(func.max(tb.c.id))).scalar_one()


def create_cateogry(name: str, color: str, conn: Connection) -> int:
    logger.info(f"Creating category: {name}")
    table = Table("category", METADATA, autoload_with=conn)
    now = datetime.datetime.now()
    conn.execute(
        insert(table).values(
            name=name,
            createdAt=now,
            updatedAt=now,
            nature="NEED",
            color=color,
        )
    )
    conn.commit()

    return list(
        conn.execute(select(table.c.id).where(table.c.name == name))
        .scalars()
        .all()
    )[0]


def create_accounts(account_names: Iterable[str], conn: Connection):
    logger.info("Creating accounts")
    account_table = Table("account", METADATA, autoload_with=conn)
    existing_accounts = set(
        conn.execute(select(account_table.c.name)).scalars().all()
    )
    for name in account_names:
        if name not in existing_accounts:
            logger.debug(f"Creating account: {name}")
            conn.execute(
                insert(account_table).values(
                    name=name,
                    description="Imported with finwrap",
                    createdAt=datetime.datetime.now(),
                    updatedAt=datetime.datetime.now(),
                    beginningBalance=0.0,
                    hidden=0,
                )
            )
    conn.commit()


def get_table(table_name: str, conn: Connection) -> pl.LazyFrame:
    logger.debug(f"Getting table: {table_name}")
    return pl.read_database(
        f"SELECT * FROM {table_name}", conn, infer_schema_length=10000
    ).lazy()


def prepare_account_names(data):
    return (
        data.select("account_name")
        .unique()
        .collect()
        .get_column("account_name")
        .to_list()
    )


def prepare_dataframe(
    data: pl.LazyFrame,
    account_table: pl.LazyFrame,
    category_id: int,
    now_expr: pl.Expr,
):
    return (
        data.join(
            account_table.select(account_name="name", accountId="id"),
            on="account_name",
            how="inner",
        )
        .select(
            createdAt=now_expr,
            updatedAt=now_expr,
            label=pl.col("transaction"),
            amount=pl.col("amount").abs(),
            date=pl.col("date"),
            accountId=pl.col("accountId"),
            categoryId=pl.lit(category_id).cast(pl.Int64),
            isIncome=(pl.col("amount") > 0),
            isInProgress=pl.lit(False),
            isTransfer=pl.lit(False),
        )
        .fill_null("N/A")
    )


def process_record_table(record_table: pl.LazyFrame):
    return record_table.select(
        createdAt=pl.col("createdAt").str.to_datetime(),
        updatedAt=pl.col("updatedAt").str.to_datetime(),
        label=pl.col("label"),
        amount=pl.col("amount"),
        date=pl.col("date").str.to_datetime(),
        accountId=pl.col("accountId"),
        categoryId=pl.col("categoryId").cast(pl.Int64),
        isIncome=pl.col("isIncome") == 1,
        isInProgress=pl.col("isInProgress") == 1,
        isTransfer=pl.col("isTransfer") == 1,
    )


def save_to_bagel(account: Account | AccountCollection):
    logger.info("Starting save to bagel")
    data = account.get_data()
    db_path = locate_database()
    engine = create_engine("sqlite:///" + str(db_path.resolve()))
    METADATA.reflect(engine)
    account_names = prepare_account_names(data)

    with engine.connect() as conn:
        create_accounts(account_names, conn)
        category_id = create_cateogry("imported", "blue", conn)
        account_table = get_table("account", conn)

        now = datetime.datetime.now()
        now_expr = pl.datetime(
            year=now.year,
            month=now.month,
            day=now.day,
            hour=now.hour,
            minute=now.minute,
            second=now.second,
            microsecond=now.microsecond,
        )

        record_table = get_table("record", conn)

        df = prepare_dataframe(data, account_table, category_id, now_expr)

        if record_table.count().collect().item(0, 0) > 0:
            logger.debug("Processing existing records")
            record_table = process_record_table(record_table)
            df = df.join(
                record_table, how="anti", on=["label", "amount", "date"]
            )

        df = df.collect()
        if df.is_empty():
            logger.info(f"Writing {len(df):,} records to database")
            df.write_database("record", conn, if_table_exists="append")
            conn.commit()
            logger.info("Save completed successfully")
        else:
            logger.info("No new records to write")


def cli():
    app = typer.Typer()

    @app.command()
    def save(
        config_path: str = typer.Argument(help="Path to configuration"),
    ):
        logger.info(f"Loading configuration from {config_path}")
        accounts = AccountCollection.load(config_path)
        save_to_bagel(accounts)

    app()
