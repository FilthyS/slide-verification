import base64
from pathlib import Path

from DrissionPage import Chromium
from DrissionPage.items import ChromiumElement, MixTab

from gap_detect import (
    BG_NATURAL_W,
    PIECE_IMG_NATURAL,
    GapNotFoundError,
    drag_slider,
    find_gap_left,
    piece_geometry,
)

print(
    "IMPORTANT: Keep your mouse pointer outside the browser tab until the "
    "slider attempt finishes; physical mouse movement overrides the "
    "DrissionPage mouse automation.",
    flush=True,
)

tab = Chromium().latest_tab
assert isinstance(tab, MixTab)
tab.get("https://www.dingxiang-inc.com/demo/captcha")


def require_element(
    root, locator: str, description: str, timeout: float = 10
) -> ChromiumElement:
    element = root.ele(locator, timeout=timeout)
    if not isinstance(element, ChromiumElement):
        raise RuntimeError(  # noqa: TRY004
            f"Timed out waiting for {description}: {locator}"
        )
    return element


slide_verification_li = require_element(
    tab,
    "css:.wrapper-captcha-2 li.item-2",
    'the "slide puzzle" navigation item',
)
slide_verification_li.click()

slider_trigger = require_element(
    tab,
    "css:.wrapper-captcha-3 li.item-2 .captcha-trigger",
    "the slider CAPTCHA trigger",
)

# The CAPTCHA SDK binds mouseenter/mouseleave to this wrapper.
slider_trigger_wrapper = require_element(
    slider_trigger,
    "css:.dx_captcha_oneclick_wrapper",
    "the SDK-injected CAPTCHA trigger wrapper",
)
if not slider_trigger_wrapper.wait.displayed(timeout=10):
    raise RuntimeError("The CAPTCHA trigger wrapper did not become visible.")

# Move outside first so a fresh mouseenter is generated even when the real
# cursor was already resting over the trigger from a previous run.
trigger_x, trigger_y = slider_trigger_wrapper.rect.location
_, trigger_height = slider_trigger_wrapper.rect.size
tab.actions.move_to((trigger_x - 5, trigger_y + trigger_height / 2), duration=0.2)
tab.actions.move_to(slider_trigger_wrapper, duration=0.5)

drag_handle = require_element(
    tab,
    "css:#dx_captcha_basic_slider_3",
    "the slider CAPTCHA drag handle after hovering",
    timeout=20,
)
if not drag_handle.wait.displayed(timeout=10):
    raise RuntimeError("The slider CAPTCHA drag handle did not become visible.")

canvas = require_element(
    tab,
    "xpath://div[@id='dx_captcha_basic_bg_3']/canvas",
    "The CAPTCHA background canvas.",
)

bg_data_url = canvas.run_js('return this.toDataURL("image/png");')
png_bytes = base64.b64decode(bg_data_url.split(",", 1)[1])
Path("./saved_img/captcha-bg.png").write_bytes(png_bytes)

slider_img = require_element(
    tab,
    "css:#dx_captcha_basic_sub-slider_3 img",
    "The slider image to match",
)
if not slider_img.wait.displayed(timeout=10):
    raise RuntimeError("The CAPTCHA puzzle piece did not become visible.")

saved_path = slider_img.save(
    path="./saved_img",
    name="captcha-slider.webp",
    timeout=10,
)

# ---------------------------------------------------------------------------
# Gap detection + drag (logic lives in gap_detect.py)
# ---------------------------------------------------------------------------

piece_x0, piece_y0, piece_x1, piece_y1 = piece_geometry(Path(saved_path))
piece_w = piece_x1 - piece_x0 + 1
piece_h = piece_y1 - piece_y0 + 1
print(f"piece bbox in img: x {piece_x0}..{piece_x1}, y {piece_y0}..{piece_y1}")

canvas_rect = canvas.rect
sub_rect = slider_img.rect
canvas_scale = canvas_rect.size[0] / BG_NATURAL_W
expected_width = sub_rect.size[0] * piece_w / PIECE_IMG_NATURAL / canvas_scale
expected_height = sub_rect.size[1] * piece_h / PIECE_IMG_NATURAL / canvas_scale
print(f"expected hole: {expected_width:.0f}x{expected_height:.0f} natural px")

try:
    gap_left = find_gap_left(
        Path("./saved_img/captcha-bg.png"),
        Path(saved_path),
        expected_width,
        expected_height,
    )
except GapNotFoundError as exc:
    raise SystemExit(f"not solved: {exc}") from exc
print(f"gap_left = {gap_left}")
assert 0 < gap_left < BG_NATURAL_W, f"gap outside the canvas: {gap_left}"
gap_left_screen = canvas_rect.location[0] + gap_left * canvas_scale
piece_left_screen = (
    sub_rect.location[0] + piece_x0 / PIECE_IMG_NATURAL * sub_rect.size[0]
)
distance = gap_left_screen - piece_left_screen
print(f"gap left: {gap_left} natural px -> drag distance {distance:.1f} px")

actual_distance = drag_slider(tab, drag_handle, distance)
print(f"dragged slider {actual_distance:.1f} px")

