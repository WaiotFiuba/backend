from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_auth: no simula el usuario autenticado; usa la autenticación real.",
    )


@pytest.fixture(autouse=True)
def authenticated_user(request):
    """Simula un usuario autenticado en los endpoints protegidos por get_current_user.

    Los tests que verifican la autenticación real usan el marcador `real_auth`.
    """
    if request.node.get_closest_marker("real_auth"):
        yield None
        return

    from app.core.deps import get_current_user
    from app.core.roles import RoleId
    from app.main import app
    from app.models.user import User

    user = User(
        id=1,
        name="Test User",
        language="es",
        email="test@example.com",
        password="not-used",
        available=True,
        role_id=int(RoleId.superadmin),
    )
    app.dependency_overrides[get_current_user] = lambda: user
    yield user
    app.dependency_overrides.pop(get_current_user, None)
