from src.config.settings import normalize_database_url


def test_postgresql_urls_use_psycopg_three_driver():
    assert normalize_database_url('postgresql://user:pw@db:5432/app') == (
        'postgresql+psycopg://user:pw@db:5432/app'
    )


def test_explicit_database_driver_is_preserved():
    url = 'postgresql+psycopg://user:pw@db:5432/app'
    assert normalize_database_url(url) == url
