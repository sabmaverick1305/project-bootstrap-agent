import pytest

from app.models.naming import (
    InvalidNameError,
    check_module_name,
    default_project_name,
    normalize_project_name,
    to_package_name,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("fastapi-app", "fastapi-app"),
        ("My API Service", "my-api-service"),
        ("orders_service", "orders-service"),
        ("  Billing--API  ", "billing-api"),
        ("app2", "app2"),
    ],
)
def test_normalize_project_name(raw, expected):
    assert normalize_project_name(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "!!!", "2fast"])
def test_normalize_project_name_rejects_invalid(raw):
    with pytest.raises(InvalidNameError):
        normalize_project_name(raw)


def test_default_project_name():
    assert default_project_name("fastapi") == "fastapi-app"


def test_to_package_name():
    assert to_package_name("my-api-service") == "my_api_service"


@pytest.mark.parametrize("name", ["typing", "test", "json", "fastapi", "pytest", "class", "MyApp"])
def test_check_module_name_rejects_unsafe_names(name):
    with pytest.raises(InvalidNameError):
        check_module_name(name)


def test_check_module_name_accepts_snake_case():
    check_module_name("health_check")
