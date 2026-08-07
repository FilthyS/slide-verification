"""Observe the rendered DingXiang verification result after a slider attempt.

The demo does not expose its CAPTCHA instance to this script, so the official
``verifySuccess``/``verifyFail`` events cannot be subscribed to directly.
Instead, a MutationObserver records the SDK's rendered terminal bar state.  A
terminal snapshot is retained even if the widget immediately resets itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time
from typing import Any, Mapping


class VerificationStatus(str, Enum):
    """Possible states observed after releasing the slider."""

    PENDING = "pending"
    SUCCESS = "success"
    FAILURE = "failure"
    LOAD_ERROR = "load_error"


@dataclass(frozen=True)
class VerificationResult:
    """A normalized verification state and its best available UI message."""

    status: VerificationStatus
    message: str = ""
    source: str = ""


class VerificationTimeoutError(RuntimeError):
    """Raised when the SDK never renders a terminal result."""


# This observer is armed before the drag. DingXiang can display a failure only
# briefly before refreshing the challenge; retaining the first terminal state
# prevents a slower Python polling interval from missing that transition.
_ARM_OBSERVER_SCRIPT = r"""
const root = this;
if (root.__slideVerificationDetector?.observer) {
    root.__slideVerificationDetector.observer.disconnect();
}

const detector = {
    latest: null,
    terminal: null,
    observer: null,
};

const displayed = (element) => {
    if (!element || element.getClientRects().length === 0) return false;
    const style = getComputedStyle(element);
    return style.visibility !== 'hidden' && style.opacity !== '0';
};
const text = (element) => (element?.textContent || '').trim();
const hasStateClass = (className, states) => {
    const classes = String(className || '').split(/\s+/);
    return states.some((state) => classes.includes(state));
};

detector.sample = () => {
    const query = (selector) => root.querySelector(selector);
    const basicBar = query('.dx_captcha_basic_bar');
    const oneClickBar = query('.dx_captcha_oneclick_bar');
    const basicSuccess = query('.dx_captcha_basic_bar-success');
    const oneClickSuccess = query('.dx_captcha_oneclick_bar-success');
    const failure = query('.dx_captcha_basic_bar-fail');
    const basicLoadError = query('.dx_captcha_basic_bar-load-fail');
    const oneClickLoadError = query('.dx_captcha_oneclick_bar-load-fail');

    const snapshot = {
        basic_bar_classes: basicBar?.className || '',
        oneclick_bar_classes: oneClickBar?.className || '',
        success_visible: displayed(basicSuccess) || displayed(oneClickSuccess),
        failure_visible: displayed(failure),
        load_error_visible:
            displayed(basicLoadError) || displayed(oneClickLoadError),
        success_message: text(
            displayed(basicSuccess) ? basicSuccess : oneClickSuccess
        ),
        failure_message: text(failure),
        load_error_message: text(
            displayed(basicLoadError) ? basicLoadError : oneClickLoadError
        ),
        observed_status: 'pending',
    };

    const barClasses = [
        snapshot.basic_bar_classes,
        snapshot.oneclick_bar_classes,
    ];
    if (
        snapshot.success_visible ||
        barClasses.some((value) => hasStateClass(value, ['dx-success']))
    ) {
        snapshot.observed_status = 'success';
    } else if (snapshot.load_error_visible) {
        snapshot.observed_status = 'load_error';
    } else if (
        snapshot.failure_visible ||
        barClasses.some((value) =>
            hasStateClass(value, ['dx-fail', 'dx-error'])
        )
    ) {
        snapshot.observed_status = 'failure';
    }

    detector.latest = snapshot;
    if (snapshot.observed_status !== 'pending' && !detector.terminal) {
        detector.terminal = snapshot;
    }
    return detector.terminal || detector.latest;
};

detector.observer = new MutationObserver(() => detector.sample());
detector.observer.observe(root, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: ['class', 'style'],
});
root.__slideVerificationDetector = detector;
return detector.sample();
"""

_READ_OBSERVER_SCRIPT = r"""
const detector = this.__slideVerificationDetector;
if (!detector) return null;
return detector.terminal || detector.sample();
"""

_STOP_OBSERVER_SCRIPT = r"""
const detector = this.__slideVerificationDetector;
if (detector?.observer) detector.observer.disconnect();
delete this.__slideVerificationDetector;
"""


def _tokens(value: object) -> set[str]:
    return set(value.split()) if isinstance(value, str) else set()


def _text(snapshot: Mapping[str, Any], key: str, fallback: str) -> str:
    value = snapshot.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def classify_verification_snapshot(
    snapshot: Mapping[str, Any],
) -> VerificationResult:
    """Normalize a raw browser snapshot into one verification state."""
    observed = snapshot.get("observed_status")
    class_tokens = _tokens(snapshot.get("basic_bar_classes")) | _tokens(
        snapshot.get("oneclick_bar_classes")
    )

    if observed == VerificationStatus.SUCCESS.value or (
        "dx-success" in class_tokens or snapshot.get("success_visible") is True
    ):
        return VerificationResult(
            VerificationStatus.SUCCESS,
            _text(snapshot, "success_message", "Verification succeeded."),
            "rendered success state",
        )
    if observed == VerificationStatus.LOAD_ERROR.value or snapshot.get(
        "load_error_visible"
    ) is True:
        return VerificationResult(
            VerificationStatus.LOAD_ERROR,
            _text(snapshot, "load_error_message", "CAPTCHA result failed to load."),
            "rendered load-error state",
        )
    if observed == VerificationStatus.FAILURE.value or (
        {"dx-fail", "dx-error"} & class_tokens
        or snapshot.get("failure_visible") is True
    ):
        return VerificationResult(
            VerificationStatus.FAILURE,
            _text(snapshot, "failure_message", "Verification was rejected."),
            "rendered failure state",
        )
    return VerificationResult(VerificationStatus.PENDING)


def _snapshot_from_root(root, script: str) -> Mapping[str, Any]:
    snapshot = root.run_js(script)
    if not isinstance(snapshot, Mapping):
        raise RuntimeError("verification observer is unavailable in the page")
    return snapshot


def arm_verification_detection(root) -> VerificationResult:
    """Start recording result-state mutations before the slider is moved."""
    return classify_verification_snapshot(
        _snapshot_from_root(root, _ARM_OBSERVER_SCRIPT)
    )


def read_verification_result(root) -> VerificationResult:
    """Read the current or first recorded terminal verification state."""
    return classify_verification_snapshot(
        _snapshot_from_root(root, _READ_OBSERVER_SCRIPT)
    )


def stop_verification_detection(root) -> None:
    """Disconnect and discard the page-side observer."""
    root.run_js(_STOP_OBSERVER_SCRIPT)


def wait_for_verification_result(
    root,
    timeout: float = 10.0,
    poll_interval: float = 0.05,
) -> VerificationResult:
    """Wait for success, rejection, or a CAPTCHA load error.

    ``arm_verification_detection()`` must be called before the drag so brief
    terminal transitions cannot be missed.
    """
    if timeout <= 0:
        raise ValueError("verification timeout must be positive")
    if poll_interval <= 0:
        raise ValueError("verification poll interval must be positive")

    deadline = time.monotonic() + timeout
    last_result = VerificationResult(VerificationStatus.PENDING)
    try:
        while True:
            last_result = read_verification_result(root)
            if last_result.status is not VerificationStatus.PENDING:
                return last_result

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VerificationTimeoutError(
                    "CAPTCHA did not report success or failure within "
                    f"{timeout:.1f}s (last state: {last_result.status.value})"
                )
            time.sleep(min(poll_interval, remaining))
    finally:
        stop_verification_detection(root)
