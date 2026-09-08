"""What the deploy job is allowed to conclude from what it can see.

The job has two sources and they answer different questions. Production answers what it is
SERVING; Coolify answers whether the deployment it was asked to make SUCCEEDED. Until
2026-09-07 the job read only the first, so a deployment that failed two seconds in was
indistinguishable from one still in flight -- both leave the previous container answering --
and the step waited out its full 600-second deadline before reporting an ambiguity.

That happened three times in twelve pushes, and on 2026-09-06 nothing corrected it: app-brain
served a build behind main for a day and a half while main showed a red run whose message said
the brain "never answered, is still on the previous build, or is on this build but unhealthy".
It was none of those. Coolify's deployment had failed and been rolled back.
"""

from pathlib import Path

WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"


def _workflow() -> str:
    return WORKFLOW.read_text()


def test_the_trigger_step_keeps_the_deployment_id_coolify_names():
    """The id is the only handle on the deployment this run asked for. Without it the verify
    step can ask Coolify about the application's LATEST deployment, which on a re-run is
    somebody else's."""
    workflow = _workflow()
    assert "deployment_uuid" in workflow
    # The WRITE, not merely the path. The file is also initialised and read by name, so an
    # assertion on the path alone stays green with the recording line deleted -- measured by
    # mutation, which is how this test came to say what it means.
    assert 'echo "${name} ${deployment}" >> "${RUNNER_TEMP}/deployments.txt"' in workflow


def test_a_response_naming_no_deployment_is_refused():
    """A 2xx that queued nothing is indistinguishable from a successful trigger everywhere
    downstream, so it has to be caught where the response is still in hand."""
    assert "names no deployment, so nothing" in _workflow()


def test_the_verify_step_asks_coolify_whether_the_deployment_failed():
    assert "/deployments/${deployment}" in _workflow()


def test_only_an_explicit_failed_ends_the_wait():
    """The load-bearing half, and the direction matters. `failed` is one of several states --
    a deployment is `in_progress` for most of its life -- so a guard written as "not finished"
    would end every run on its first pass, and one written as "not failed" would never end any.
    Nothing here may conclude from a status it could not read: an unreachable Coolify must cost
    a slower failure, never a wrong one, because the revision poll is still the authority on
    success and the deadline still binds."""
    workflow = _workflow()
    assert '"${state}" = "failed"' in workflow
    assert '"${state}" != "finished"' not in workflow


def test_the_revision_poll_survives_the_status_read():
    """Coolify reporting `finished` and production SERVING the revision are different facts,
    and the four brains swap at measurably different times off one image. A status read that
    replaced the poll would pass while three of four served the previous build."""
    workflow = _workflow()
    # The COMPARISON, not a message mentioning it. `is serving ${EXPECTED}` also occurs inside
    # the timeout's own wording, so asserting the phrase stays green with the poll gone.
    assert '[ "${seen}" = "${EXPECTED}" ] && [ "${state}" = "ok" ]' in workflow


def test_the_api_base_is_derived_and_never_guessed():
    """Two secrets naming one Coolify can disagree, and the wrong one fails as a 404 nobody
    attributes. Derived from the webhook URL, and refused rather than guessed if that URL is
    not the shape the derivation assumes."""
    workflow = _workflow()
    assert 'api_base="${WEBHOOK_URL%/deploy}"' in workflow
    assert "cannot be derived from it" in workflow
