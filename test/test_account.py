import os
import tempfile
from pathlib import Path

import pytest
from finwrap import Account


@pytest.fixture
def sample_csv():
    # Create a temporary CSV file with sample data
    with tempfile.NamedTemporaryFile(
        suffix=".csv", delete=False, mode="w"
    ) as f:
        f.write("date,amount,description,fees\n")
        f.write("2023-01-01,100.00,Test Transaction 1,1.00\n")
        f.write("2023-01-02,200.00,Test Transaction 2,2.00\n")
    yield Path(f.name)
    os.unlink(f.name)


@pytest.fixture
def sample_account(sample_csv):
    return Account(
        file_path=sample_csv,
        name="Test Account",
        date_col="date",
        amount_col="amount",
        transaction_col="description",
        fees_col="fees",
        date_col_format="%Y-%m-%d",
    )


def test_save_and_load(sample_account):
    # Create a temporary file for saving
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as temp_file:
        temp_path = temp_file.name

    try:
        # Test save
        sample_account.save(temp_path)
        assert os.path.exists(temp_path)

        # Test load
        loaded_account = Account.load(temp_path)

        # Verify that the loaded account has the same attributes
        assert loaded_account.name == sample_account.name
        assert loaded_account.date_col == sample_account.date_col
        assert loaded_account.amount_col == sample_account.amount_col
        assert loaded_account.transaction_col == sample_account.transaction_col
        assert loaded_account.fees_col == sample_account.fees_col
        assert loaded_account.date_col_format == sample_account.date_col_format

        # Test that file_path is preserved
        assert str(loaded_account.file_path) == str(sample_account.file_path)

    finally:
        # Cleanup
        os.unlink(temp_path)


def test_save_with_invalid_path(sample_account):
    # Test saving to an invalid path
    with pytest.raises(IOError):
        sample_account.save("/nonexistent/directory/file.yaml")


def test_load_nonexistent_file():
    # Test loading a non-existent file
    with pytest.raises(FileNotFoundError):
        Account.load("nonexistent.yaml")


def test_load_invalid_yaml():
    # Create a file with invalid YAML content
    with tempfile.NamedTemporaryFile(
        suffix=".yaml", delete=False, mode="w"
    ) as temp_file:
        temp_file.write("invalid: yaml: content:")
        temp_path = temp_file.name

    try:
        with pytest.raises(
            Exception
        ):  # Should raise some form of YAML parsing error
            Account.load(temp_path)
    finally:
        os.unlink(temp_path)


def test_save_and_load_with_multiple_files(sample_csv):
    # Create another temporary CSV file
    with tempfile.NamedTemporaryFile(
        suffix=".csv", delete=False, mode="w"
    ) as f:
        f.write("date,amount,description,fees\n")
        f.write("2023-01-03,300.00,Test Transaction 3,3.00\n")
        second_csv = Path(f.name)

    account = Account(
        file_path=[sample_csv, second_csv],
        name="Test Account",
        date_col="date",
        amount_col="amount",
        transaction_col="description",
        fees_col="fees",
    )

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as temp_file:
        temp_path = temp_file.name

    try:
        account.save(temp_path)
        loaded_account = Account.load(temp_path)

        # Verify that the list of file paths is preserved
        assert isinstance(loaded_account.file_path, list)
        assert len(loaded_account.file_path) == 2
        assert isinstance(account.file_path, list)
        assert all(
            str(p1) == str(p2)
            for p1, p2 in zip(loaded_account.file_path, account.file_path)
        )

    finally:
        os.unlink(temp_path)
        os.unlink(second_csv)
