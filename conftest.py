import os
import shutil
import time
import threading
import pytest
import requests
from os import getenv, remove


def jot_teardown(tmp_catnote: str, fixed_catnote: str):
    """Shared teardown for test classes that write to a temporary .jot file."""
    try:
        remove(tmp_catnote)
    except FileNotFoundError:
        pass
    try:
        remove(f"{tmp_catnote}.new")
    except FileNotFoundError:
        pass
    if os.path.exists(f"{fixed_catnote}.old"):
        shutil.move(f"{fixed_catnote}.old", fixed_catnote)


_LLM_CLASSES = {
    "TestExtractSceneContext",
    "TestToolDispatch",
    "TestRunToolLoop",
    "TestCondenseContext",
    "TestRecordKnowledgeLive",
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
