import pytest

from app.main import cors_options


@pytest.mark.parametrize(
    ("raw", "expected_origins", "expected_credentials"),
    [
        ("", ["*"], False),  # sin configurar: cualquier origen, sin credenciales
        ("  ,  ", ["*"], False),
        ("*", ["*"], False),
        (
            "https://app.example.com, http://localhost:5173",
            ["https://app.example.com", "http://localhost:5173"],
            True,
        ),
        ("https://app.example.com,*", ["https://app.example.com", "*"], False),
    ],
)
def test_cors_options(raw, expected_origins, expected_credentials):
    origins, allow_credentials = cors_options(raw)

    assert origins == expected_origins
    assert allow_credentials is expected_credentials
