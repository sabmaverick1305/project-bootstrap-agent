import pytest

from app.models.intent import IntentDraft, IntentError, RequestedItem, build_spec


def _draft(*items, **kwargs) -> IntentDraft:
    requested = [RequestedItem(category=c, value=v, version=ver) for c, v, ver in items]
    return IntentDraft(is_project_request=True, requested=requested, **kwargs)


def test_basic_request_marks_explicit_choices_as_user_specified():
    result = build_spec(_draft(("language", "python", None), ("framework", "FastAPI", None)))

    spec = result.spec
    assert result.question is None
    assert spec.project_name == "fastapi-app"
    assert spec.user_specified == {"language", "framework"}
    assert spec.unsupported_requests == []
    assert spec.language_version is None  # left for the lookup step


def test_supported_defaults_named_by_user_are_user_specified():
    spec = build_spec(
        _draft(
            ("framework", "fastapi", None),
            ("package_manager", "uv", None),
            ("testing", "pytest", None),
        )
    ).spec

    assert spec.user_specified == {"framework", "package_manager", "testing"}


def test_explicit_versions_are_kept():
    spec = build_spec(
        _draft(
            ("language", "python", "3.13.2"),
            ("framework", "fastapi", "0.115.0"),
            ("testing", "pytest", "8.3.0"),
        )
    ).spec

    assert spec.language_version == "3.13"
    assert spec.framework_version == "0.115.0"
    assert {d.name: d.version for d in spec.dev_dependencies}["pytest"] == "8.3.0"
    assert {"language_version", "framework_version"} <= spec.user_specified


def test_project_name_is_normalized_and_user_specified():
    spec = build_spec(_draft(("framework", "fastapi", None), project_name="Orders API")).spec

    assert spec.project_name == "orders-api"
    assert spec.package_name == "orders_api"
    assert "project_name" in spec.user_specified


def test_unsupported_extras_are_recorded_not_dropped():
    spec = build_spec(
        _draft(
            ("framework", "fastapi", None),
            ("database", "postgresql", None),
            ("containerization", "docker", None),
            ("package_manager", "poetry", None),
        )
    ).spec

    assert spec.unsupported_requests == [
        "database: postgresql (not supported yet; it will be left out)",
        "containerization: docker (not supported yet; it will be left out)",
        "package_manager: poetry (not supported yet; uv will be used)",
    ]
    assert spec.database is None
    assert spec.package_manager == "uv"
    assert "package_manager" not in spec.user_specified


@pytest.mark.parametrize(
    ("category", "value"),
    [("framework", "flask"), ("framework", "django"), ("language", "typescript")],
)
def test_other_language_or_framework_is_rejected(category, value):
    with pytest.raises(IntentError, match="Only Python \\+ FastAPI"):
        build_spec(_draft((category, value, None)))


def test_clarification_question_is_returned():
    result = build_spec(_draft(clarification_question="What kind of service do you want?"))

    assert result.spec is None
    assert result.question == "What kind of service do you want?"


def test_non_project_request_is_rejected():
    with pytest.raises(IntentError, match="doesn't look like"):
        build_spec(IntentDraft(is_project_request=False))


@pytest.mark.parametrize(
    ("items", "kwargs", "message"),
    [
        ([], {"project_name": "fastapi"}, "shadow"),
        ([("language", "python", "latest-ish")], {}, "not a valid version"),
        ([("framework", "fastapi", "1.0.0rc1")], {}, "pre-release"),
    ],
)
def test_invalid_values_raise_intent_error(items, kwargs, message):
    with pytest.raises(IntentError, match=message):
        build_spec(_draft(*items, **kwargs))
