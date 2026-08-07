"""Regression checks for browser-rendered verification result detection."""

from verification_detect import (
    VerificationStatus,
    VerificationTimeoutError,
    arm_verification_detection,
    classify_verification_snapshot,
    wait_for_verification_result,
)


def snapshot(**updates) -> dict[str, object]:
    result: dict[str, object] = {
        "basic_bar_classes": "dx_captcha_basic_bar",
        "oneclick_bar_classes": "dx_captcha_oneclick_bar",
        "success_visible": False,
        "failure_visible": False,
        "load_error_visible": False,
        "success_message": "验证成功",
        "failure_message": "验证未通过",
        "load_error_message": "加载失败，请点击重试！",
        "observed_status": "pending",
    }
    result.update(updates)
    return result


result = classify_verification_snapshot(snapshot())
assert result.status is VerificationStatus.PENDING
print("ok: neutral bar state remains pending")

result = classify_verification_snapshot(
    snapshot(basic_bar_classes="dx_captcha_basic_bar dx-success")
)
assert result.status is VerificationStatus.SUCCESS
assert result.message == "验证成功"
print("ok: dx-success class is accepted as success")

result = classify_verification_snapshot(snapshot(failure_visible=True))
assert result.status is VerificationStatus.FAILURE
assert result.message == "验证未通过"
print("ok: visible rejection message is classified as failure")

result = classify_verification_snapshot(snapshot(load_error_visible=True))
assert result.status is VerificationStatus.LOAD_ERROR
print("ok: CAPTCHA load errors are distinct from rejected attempts")

# The observer's recorded terminal state takes priority after the widget resets.
result = classify_verification_snapshot(snapshot(observed_status="failure"))
assert result.status is VerificationStatus.FAILURE
print("ok: retained transient failure is not lost after a widget reset")


class FakeRoot:
    def __init__(self, states: list[dict[str, object]]) -> None:
        self.states = states
        self.index = 0
        self.armed = False
        self.stopped = False

    def run_js(self, script: str):
        if "new MutationObserver" in script:
            self.armed = True
            return self.states[0]
        if "disconnect()" in script and "delete this" in script:
            self.stopped = True
            return None
        self.index = min(self.index + 1, len(self.states) - 1)
        return self.states[self.index]


fake_root = FakeRoot(
    [snapshot(), snapshot(), snapshot(observed_status="success")]
)
assert arm_verification_detection(fake_root).status is VerificationStatus.PENDING
result = wait_for_verification_result(
    fake_root, timeout=0.1, poll_interval=0.001
)
assert result.status is VerificationStatus.SUCCESS
assert fake_root.armed and fake_root.stopped
print("ok: polling returns success and always disconnects the observer")

timeout_root = FakeRoot([snapshot()])
arm_verification_detection(timeout_root)
try:
    wait_for_verification_result(
        timeout_root, timeout=0.002, poll_interval=0.001
    )
except VerificationTimeoutError:
    assert timeout_root.stopped
    print("ok: missing terminal state times out and cleans up")
else:
    raise AssertionError("pending verification did not time out")
