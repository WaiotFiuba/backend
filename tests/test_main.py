from app.main import read_root


def test_root_returns_hello_world():
    assert read_root() == {"message": "Hello World"}
