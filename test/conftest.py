import codecs
from pathlib import Path

import pandas as pd
import pytest


TEST_DIR = Path(__file__).resolve().parent


@pytest.fixture
def html_page():
    # 'utf-8' codec can't decode byte 0xed in position 19806: invalid continuation byte
    with codecs.open(TEST_DIR / 'mock' / 'html_page.html', 'r', encoding='latin-1') as f:
        return f.read()

@pytest.fixture
def df_estates():
    df_path = TEST_DIR / 'mock' / 'df_estates.csv'
    if df_path.exists():
        return pd.read_csv(df_path)
    # Minimal fixture for tests that only assert file writing.
    return pd.DataFrame([
        {
            'url': '/propiedades/fake.html',
            'price_value': 123,
            'price_type': 'USD',
        }
    ])
