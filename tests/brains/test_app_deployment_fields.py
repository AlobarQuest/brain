"""Unit tests for the app-brain deployment-fields feature:
  - pure backfill helpers (normalize_github_repo, extract_environments)
  - the get_app contract shape (serialize_app_profile)

No DB required — matches the mock-based style of tests/brains/test_app.py.
"""
import importlib.util
import pathlib

# The backfill script lives under scripts/ (not a package); load it by path.
_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "backfill_app_deployment_fields.py"
_spec = importlib.util.spec_from_file_location("backfill_app_deployment_fields", _SCRIPT)
backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill)

normalize_github_repo = backfill.normalize_github_repo
extract_environments = backfill.extract_environments

# The exact deployment chunk content from booking-assistant in production.
BOOKING_DEPLOY_CHUNK = (
    "Production deploys from the master branch to https://booking.devonwatkins.com "
    "via Coolify; preview deploys from the preview branch to "
    "https://preview.booking.devonwatkins.com. The app runs in Docker on port 8080 "
    "with SQLite persisted at /data/booking.db."
)


class TestNormalizeGithubRepo:
    def test_https_with_git_suffix(self):
        assert (
            normalize_github_repo("https://github.com/AlobarQuest/booking-system.git")
            == "AlobarQuest/booking-system"
        )

    def test_https_without_git_suffix(self):
        assert (
            normalize_github_repo("https://github.com/AlobarQuest/booking-system")
            == "AlobarQuest/booking-system"
        )

    def test_scp_style(self):
        assert (
            normalize_github_repo("git@github.com:AlobarQuest/booking-system.git")
            == "AlobarQuest/booking-system"
        )

    def test_trailing_slash(self):
        assert (
            normalize_github_repo("https://github.com/AlobarQuest/brain/")
            == "AlobarQuest/brain"
        )

    def test_non_github_returns_none(self):
        assert normalize_github_repo("https://bitbucket.org/foo/bar.git") is None

    def test_empty_and_none(self):
        assert normalize_github_repo(None) is None
        assert normalize_github_repo("") is None
        assert normalize_github_repo("   ") is None

    def test_garbage_returns_none(self):
        assert normalize_github_repo("not-a-url") is None


class TestExtractEnvironments:
    def test_booking_reference_case(self):
        """The validated end-to-end reference from the task spec."""
        envs = extract_environments([BOOKING_DEPLOY_CHUNK])
        assert envs == [
            {
                "name": "prod",
                "branch": "master",
                "url": "https://booking.devonwatkins.com",
                "coolify_app_uuid": None,
            },
            {
                "name": "preview",
                "branch": "preview",
                "url": "https://preview.booking.devonwatkins.com",
                "coolify_app_uuid": None,
            },
        ]

    def test_no_match_returns_empty(self):
        assert extract_environments([
            "The app runs in Docker on port 8000 with Postgres. Deployed via Coolify."
        ]) == []

    def test_branch_without_url(self):
        envs = extract_environments(["Production deploys from the main branch."])
        assert envs == [
            {"name": "prod", "branch": "main", "url": None, "coolify_app_uuid": None}
        ]

    def test_backtick_wrapped_branch(self):
        envs = extract_environments([
            "Production deploys from the `release` branch to https://x.example.com."
        ])
        assert envs[0]["branch"] == "release"

    def test_dedup_first_match_wins(self):
        envs = extract_environments([
            "Production deploys from the master branch to https://a.example.com.",
            "Production deploys from the old branch to https://b.example.com.",
        ])
        prod = [e for e in envs if e["name"] == "prod"]
        assert len(prod) == 1
        assert prod[0]["branch"] == "master"

    def test_does_not_match_devonwatkins_hostname(self):
        """'dev' must not be picked out of a hostname like devonwatkins.com."""
        assert extract_environments([
            "The app is hosted at https://app.devonwatkins.com behind Cloudflare."
        ]) == []

    def test_deterministic_order_prod_before_preview(self):
        envs = extract_environments([
            "preview deploys from the preview branch to https://p.example.com; "
            "production deploys from the main branch to https://x.example.com."
        ])
        assert [e["name"] for e in envs] == ["prod", "preview"]

    def test_empty_input(self):
        assert extract_environments([]) == []
        assert extract_environments(["", None]) == []


class TestSerializeAppProfile:
    """The get_app response includes the new fields with the documented keys."""

    class _FakeApp:
        slug = "booking-assistant"
        name = "Booking Assistant"
        description = "desc"
        tech_stack = {"language": "Python"}
        repo_path = "/Users/devon/Projects/BookingAssistant"
        deployment_url = "https://booking.devonwatkins.com"
        github_repo = "AlobarQuest/booking-system"
        environments = [
            {"name": "prod", "branch": "master", "url": "https://booking.devonwatkins.com", "coolify_app_uuid": None},
        ]
        status = "active"
        tags = ["fastapi"]
        onboarding_status = "complete"
        last_onboarded_at = None
        created_at = None

    def test_contract_includes_new_fields(self):
        from src.brains.app.tools.apps import serialize_app_profile

        profile = serialize_app_profile(self._FakeApp(), coverage={"deployment": 2})
        assert profile["github_repo"] == "AlobarQuest/booking-system"
        assert profile["environments"] == self._FakeApp.environments
        # Existing contract keys remain present (additive, nothing removed).
        for key in ("slug", "name", "tech_stack", "repo_path", "deployment_url",
                    "status", "tags", "onboarding_status", "coverage"):
            assert key in profile
