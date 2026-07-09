import glob
import hashlib
import os
import shutil
import time
import threading
import pytest
import requests
from os import getenv, remove

_HERE = os.path.dirname(os.path.abspath(__file__))
_TESTS_DIR = os.path.join(_HERE, "tests")


def jot_teardown(tmp_catnote: str, fixed_catnote: str = None):
    """Shared teardown for tests that write to scratch .jot files under local/.

    Removes the scratch note plus any .new/.old shadow copies catjot leaves during
    delete/amend/commit. Nothing under tests/ is touched — those fixtures are
    read-only (enforced by the _guard_readonly_fixtures autouse fixture below).
    """
    for base in (tmp_catnote, "local/scratch/example.jot"):
        if not base:
            continue
        for p in (base, f"{base}.new", f"{base}.old"):
            try:
                remove(p)
            except FileNotFoundError:
                pass


def _snapshot_tests_jot():
    """Map every tests/*.jot path to its current bytes (read-only baseline)."""
    return {
        p: open(p, "rb").read()
        for p in glob.glob(os.path.join(_TESTS_DIR, "*.jot"))
    }


# Session-wide pristine baseline of the read-only fixtures, captured at import.
_TESTS_BASELINE = _snapshot_tests_jot()


@pytest.fixture(autouse=True)
def _guard_readonly_fixtures():
    """Fail (and restore) if any test mutates a tests/*.jot fixture.

    tests/ is read-only: catjot/rpjot must route every write to local/. If a test
    leaves a fixture changed, restore it from the pristine baseline so later tests
    and the working tree stay clean, then fail this test to pinpoint the offender.
    """
    yield
    changed = []
    for path, original in _TESTS_BASELINE.items():
        try:
            current = open(path, "rb").read()
        except FileNotFoundError:
            current = None
        if current != original:
            changed.append(os.path.relpath(path, _HERE))
            with open(path, "wb") as fh:
                fh.write(original)
    # Also delete any stray shadow copies a write left inside tests/.
    for stray in glob.glob(os.path.join(_TESTS_DIR, "*.jot.new")) + glob.glob(
        os.path.join(_TESTS_DIR, "*.jot.old")
    ):
        os.remove(stray)
        changed.append(os.path.relpath(stray, _HERE))
    assert not changed, (
        "test wrote to read-only tests/ fixtures (restored): "
        + ", ".join(sorted(changed))
        + " — route writes to local/ instead."
    )


_LLM_CLASSES = {
    "TestExtractSceneContext",
    "TestToolDispatch",
    "TestRunToolLoop",
    "TestCondenseContext",
    "TestRecordKnowledgeLive",
    "TestProductionActivation",
    "TestObjectActivationLive",
    "TestCartographerLive",
}

_LLM_PREFIXES = ("test_examine_location",)

# Only one LLM test may proceed at a time (guards against pytest-xdist parallelism).
_llm_semaphore = threading.Semaphore(1)


def pytest_configure(config):
    """Normalize https://localhost → http://localhost so plain-HTTP local servers work."""
    url = getenv("openai_api_url", "")
    if url.startswith("https://localhost") or url.startswith("https://127.0.0.1"):
        os.environ["openai_api_url"] = "http" + url[5:]


def pytest_collection_modifyitems(config, items):
    """Skip LLM-dependent tests when API env vars are not configured."""
    if getenv("openai_api_url"):
        return

    skip = pytest.mark.skip(
        reason="LLM not configured — set openai_api_url, openai_api_key, openai_api_model"
    )
    for item in items:
        cls = item.cls.__name__ if item.cls else ""
        if cls in _LLM_CLASSES:
            item.add_marker(skip)
        elif cls == "TestToolHandlerOutput" and any(
            item.name.startswith(p) for p in _LLM_PREFIXES
        ):
            item.add_marker(skip)


def _is_llm_test(item):
    cls = item.cls.__name__ if item.cls else ""
    if cls in _LLM_CLASSES:
        return True
    if cls == "TestToolHandlerOutput" and any(
        item.name.startswith(p) for p in _LLM_PREFIXES
    ):
        return True
    return False


@pytest.fixture(autouse=True)
def _llm_serializer(request):
    """Serialize all LLM tests; add a brief cooldown after each call."""
    if _is_llm_test(request.node):
        with _llm_semaphore:
            yield
            time.sleep(0.5)  # brief cooldown so local server isn't slammed back-to-back
    else:
        yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Convert HTTP 5xx errors in LLM tests to skips.

    A 5xx means the LLM server was unavailable, not that the code is wrong.
    Showing it as 's' (skipped) instead of 'F' (failed) prevents false negatives.
    """
    outcome = yield
    if call.when not in ("call", "setup"):
        return
    rep = outcome.get_result()
    if not rep.failed:
        return
    if not _is_llm_test(item):
        return

    excinfo = call.excinfo
    if excinfo is None:
        return

    # requests.exceptions.HTTPError with a 5xx status
    if issubclass(excinfo.type, requests.exceptions.HTTPError):
        exc = excinfo.value
        resp = getattr(exc, "response", None)
        if resp is not None and 500 <= resp.status_code < 600:
            rep.outcome = "skipped"
            rep.wasxfail = (
                f"LLM server returned {resp.status_code} — server unavailable"
            )
            return

    # ConnectionError / Timeout — server completely unreachable
    if issubclass(
        excinfo.type,
        (requests.exceptions.ConnectionError, requests.exceptions.Timeout),
    ):
        rep.outcome = "skipped"
        rep.wasxfail = f"LLM server unreachable ({excinfo.type.__name__})"
