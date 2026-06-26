"""Unit tests for the Coolify->app-brain deployment sync (no DB, no network).

The decisive test is `test_booking_reproduces_gold_unchanged`: building from booking's
live Coolify shape must reproduce the hand-verified gold record EXACTLY — that proves
the derivation matches the verified standard and the sync is idempotent on booking.
"""
import importlib.util
import pathlib

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "sync_deployment_from_coolify.py"
_spec = importlib.util.spec_from_file_location("sync_deployment_from_coolify", _SCRIPT)
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

derive_repo = sync.derive_repo
pick_fqdn = sync.pick_fqdn
build_environment = sync.build_environment
map_coolify_to_app = sync.map_coolify_to_app

# Booking's live Coolify applications (prod + preview), as listed by Coolify.
BOOKING_PROD = {
    "uuid": "hkw488ggssgcskk0ooc0ksk0", "name": "alobar-quest/booking-system:main-sgkoo800ooosg8ogk0w8c0w0",
    "git_repository": "AlobarQuest/booking-system", "git_branch": "master",
    "fqdn": "https://booking.devonwatkins.com", "instance": "prod",
}
BOOKING_PREVIEW = {
    "uuid": "yscogs0wggcgco8g4wwk0o0g", "name": "alobar-quest/booking-system:preview-mcw04cswog848gco848s8kkk",
    "git_repository": "AlobarQuest/booking-system", "git_branch": "preview",
    "fqdn": "https://yscogs0wggcgco8g4wwk0o0g.178.156.247.239.sslip.io,https://preview.booking.devonwatkins.com",
    "instance": "prod",
}

# The hand-verified gold record currently stored on booking-assistant.
GOLD_GITHUB_REPO = "AlobarQuest/booking-system"
GOLD_ENVIRONMENTS = [
    {"name": "prod", "branch": "master", "url": "https://booking.devonwatkins.com", "coolify_app_uuid": "hkw488ggssgcskk0ooc0ksk0"},
    {"name": "preview", "branch": "preview", "url": "https://preview.booking.devonwatkins.com", "coolify_app_uuid": "yscogs0wggcgco8g4wwk0o0g"},
]


class TestDeriveRepo:
    def test_ssh(self):
        assert derive_repo("git@github.com:AlobarQuest/booking-system.git") == "AlobarQuest/booking-system"

    def test_https(self):
        assert derive_repo("https://github.com/AlobarQuest/booking-system.git") == "AlobarQuest/booking-system"

    def test_bare_owner_repo(self):
        assert derive_repo("AlobarQuest/booking-system") == "AlobarQuest/booking-system"

    def test_trailing_slash_and_case_preserved(self):
        assert derive_repo("https://github.com/AlobarQuest/Brain/") == "AlobarQuest/Brain"

    def test_unparseable(self):
        assert derive_repo(None) is None
        assert derive_repo("") is None
        assert derive_repo("just-one-segment") is None


class TestPickFqdn:
    def test_prefers_custom_over_sslip(self):
        assert pick_fqdn(BOOKING_PREVIEW["fqdn"]) == "https://preview.booking.devonwatkins.com"

    def test_single_custom(self):
        assert pick_fqdn("https://booking.devonwatkins.com") == "https://booking.devonwatkins.com"

    def test_only_sslip_or_ip_falls_back_not_dropped(self):
        assert pick_fqdn("https://abc.178.156.247.239.sslip.io") == "https://abc.178.156.247.239.sslip.io"
        assert pick_fqdn("http://178.156.247.239") == "http://178.156.247.239"

    def test_adds_scheme_and_trims_slash(self):
        assert pick_fqdn("booking.devonwatkins.com/") == "https://booking.devonwatkins.com"

    def test_none(self):
        assert pick_fqdn(None) is None
        assert pick_fqdn("") is None


class TestBuildEnvironment:
    def test_prod(self):
        assert build_environment(BOOKING_PROD) == GOLD_ENVIRONMENTS[0]

    def test_preview_picks_custom_domain(self):
        assert build_environment(BOOKING_PREVIEW) == GOLD_ENVIRONMENTS[1]

    def test_name_from_preview_branch(self):
        env = build_environment({"uuid": "u", "git_branch": "preview", "fqdn": "https://x.example.com"})
        assert env["name"] == "preview"

    def test_null_branch_kept(self):
        env = build_environment({"uuid": "u", "git_branch": None, "fqdn": "https://x.example.com"})
        assert env["branch"] is None and env["name"] == "prod"


class TestMapCoolifyToApp:
    APPBRAIN = [
        {"slug": "booking-assistant", "github_repo": GOLD_GITHUB_REPO,
         "deployment_url": "https://booking.devonwatkins.com", "environments": GOLD_ENVIRONMENTS},
        {"slug": "watchtower", "github_repo": "AlobarQuest/watchtower", "deployment_url": None, "environments": []},
    ]

    def test_booking_reproduces_gold_unchanged(self):
        """THE GATE: live Coolify data -> exactly the verified gold record."""
        computed, unmapped = map_coolify_to_app([BOOKING_PROD, BOOKING_PREVIEW], self.APPBRAIN)
        assert computed["booking-assistant"]["github_repo"] == GOLD_GITHUB_REPO
        assert computed["booking-assistant"]["environments"] == GOLD_ENVIRONMENTS
        assert unmapped == []

    def test_orders_prod_before_preview_regardless_of_input_order(self):
        computed, _ = map_coolify_to_app([BOOKING_PREVIEW, BOOKING_PROD], self.APPBRAIN)
        assert [e["name"] for e in computed["booking-assistant"]["environments"]] == ["prod", "preview"]

    def test_unmapped_coolify_app_logged_not_invented(self):
        stray = {"uuid": "z9", "name": "alobar-quest/ghost:main-x", "git_repository": "AlobarQuest/ghost-app",
                 "git_branch": "main", "fqdn": "https://ghost.example.com", "instance": "prod"}
        computed, unmapped = map_coolify_to_app([stray], self.APPBRAIN)
        assert computed == {}
        assert len(unmapped) == 1 and unmapped[0]["uuid"] == "z9"

    def test_fqdn_fallback_when_repo_absent(self):
        # A Coolify app with no git_repository but a known fqdn still maps.
        c = {"uuid": "u1", "name": "n", "git_repository": None, "git_branch": "master",
             "fqdn": "https://booking.devonwatkins.com", "instance": "prod"}
        computed, unmapped = map_coolify_to_app([c], self.APPBRAIN)
        assert "booking-assistant" in computed and unmapped == []

    def test_case_insensitive_repo_match(self):
        c = dict(BOOKING_PROD, git_repository="git@github.com:alobarquest/booking-system.git")
        computed, _ = map_coolify_to_app([c], self.APPBRAIN)
        assert "booking-assistant" in computed

    def test_dockerimage_placeholder_never_written_as_github_repo(self):
        """fqdn-matched dockerimage apps report git_repository='coollabsio/coolify';
        that placeholder must NOT become the app's github_repo (it maps by fqdn only)."""
        appbrain = [{"slug": "infra-brain", "github_repo": "AlobarQuest/brain",
                     "deployment_url": "https://infra-brain.devonwatkins.com", "environments": []}]
        coolify = [{"uuid": "m10", "name": "brain-infra", "git_repository": "coollabsio/coolify",
                    "git_branch": "main", "fqdn": "https://infra-brain.devonwatkins.com", "instance": "prod"}]
        computed, unmapped = map_coolify_to_app(coolify, appbrain)
        assert unmapped == []
        assert computed["infra-brain"]["github_repo"] is None  # not the placeholder
        assert computed["infra-brain"]["environments"][0]["coolify_app_uuid"] == "m10"
        assert computed["infra-brain"]["environments"][0]["branch"] == "main"
