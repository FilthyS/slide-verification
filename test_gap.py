"""Synthetic regression checks for detection and dragging."""

import tempfile
from pathlib import Path

import cv2
import numpy as np

from gap_detect import GapNotFoundError, drag_slider, find_gap_left

rng = np.random.default_rng(42)
H, W = 200, 400
HOLE_X, HOLE_Y, HOLE_W, HOLE_H = 200, 70, 44, 41


def make_pair(
    dim: float | None,
    outline_only: bool = False,
    with_decoy: bool = False,
    with_glow: bool = False,
    strong_scene_edges: bool = False,
    hole_x: int = HOLE_X,
    hole_y: int = HOLE_Y,
) -> tuple[Path, Path]:
    tmp = Path(tempfile.mkdtemp())
    scene = rng.integers(90, 220, size=(H, W)).astype(np.float32)
    if strong_scene_edges:
        cv2.rectangle(scene, (15, 20), (150, 150), 8, thickness=7)
        cv2.line(scene, (20, 175), (180, 15), 245, thickness=6)
    ys, xs = np.mgrid[0:HOLE_H, 0:HOLE_W]
    mask = (xs / HOLE_W + ys / HOLE_H) >= 0.35  # slanted cut

    # piece = the original (undimmed) cutout
    region = scene[hole_y : hole_y + HOLE_H, hole_x : hole_x + HOLE_W]
    piece = np.zeros((68, 68, 4), np.uint8)
    piece[9 : 9 + HOLE_H, 5 : 5 + HOLE_W, :3] = region[:, :, np.newaxis].repeat(
        3, axis=2
    )
    core = np.zeros((68, 68), np.uint8)
    core[9 : 9 + HOLE_H, 5 : 5 + HOLE_W] = mask.astype(np.uint8)
    if with_glow:
        alpha = cv2.dilate(
            core, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
        )
        glow = (alpha > 0) & (core == 0)
        piece[glow, :3] = (0, 255, 0)
    else:
        alpha = core
    piece[:, :, 3] = alpha * 255
    assert cv2.imwrite(str(tmp / "piece.png"), piece)

    # then alter the background at the hole
    if outline_only or with_decoy:
        eroded = mask.copy()
        padded = np.pad(mask, 1)
        for dy in range(3):
            for dx in range(3):
                eroded &= padded[dy : dy + HOLE_H, dx : dx + HOLE_W]
        border = mask & ~eroded
        target = scene[hole_y : hole_y + HOLE_H, hole_x : hole_x + HOLE_W]
        target[border] = 5
        if with_decoy:
            # Destroy content correlation in the true slot and add a brighter
            # slot-shaped decoy elsewhere. Brightness must decide the match.
            target[eroded] = rng.integers(15, 35, size=(HOLE_H, HOLE_W))[eroded]
            decoy = scene[hole_y : hole_y + HOLE_H, 70 : 70 + HOLE_W]
            decoy[border] = 5
            decoy[eroded] *= 0.8
    elif dim is not None:
        scene[hole_y : hole_y + HOLE_H, hole_x : hole_x + HOLE_W][mask] *= dim

    assert cv2.imwrite(str(tmp / "bg.png"), scene.clip(0, 255).astype(np.uint8))
    return tmp / "bg.png", tmp / "piece.png"


bg, piece = make_pair(dim=0.35)
gap = find_gap_left(bg, piece, expected_width=HOLE_W, expected_height=HOLE_H)
assert abs(gap - HOLE_X) <= 2, f"planted hole at {HOLE_X}, detector said {gap}"
print(f"ok: planted hole at x={HOLE_X}, detector found x={gap}")

# no dimmed hole anywhere -> must fail cleanly
plain_bg, plain_piece = make_pair(dim=None)
try:
    find_gap_left(plain_bg, plain_piece, HOLE_W, HOLE_H)
except GapNotFoundError:
    print("ok: no-hole scene raises GapNotFoundError")
else:
    raise AssertionError("detector guessed a hole where none exists")

# Non-rectangular variant with an outlined hole but no dimmed interior.
outline_bg, outline_piece = make_pair(dim=None, outline_only=True)
gap = find_gap_left(outline_bg, outline_piece, HOLE_W, HOLE_H)
assert abs(gap - HOLE_X) <= 3, f"outlined hole at {HOLE_X}, detector said {gap}"
print(f"ok: outlined hole found at x={gap}")

decoy_bg, decoy_piece = make_pair(dim=None, with_decoy=True)
gap = find_gap_left(decoy_bg, decoy_piece, HOLE_W, HOLE_H)
assert abs(gap - HOLE_X) <= 3, f"dimmer slot at {HOLE_X}, detector said {gap}"
print(f"ok: dimmer slot selected over matching distraction at x={gap}")

# A wide neon glow surrounds the actual piece contour, while unrelated scene
# edges are stronger than the slot. The returned x aligns the outer piece bbox.
glow_bg, glow_piece = make_pair(
    dim=None, with_decoy=True, with_glow=True, strong_scene_edges=True
)
gap = find_gap_left(glow_bg, glow_piece, HOLE_W + 10, HOLE_H + 10)
expected_glow_x = HOLE_X - 5
assert abs(gap - expected_glow_x) <= 5, (
    f"glowing piece should align at {expected_glow_x}, detector said {gap}"
)
print(f"ok: inner contour ignores glow and strong scene edges at x={gap}")

# Boundary placement must not depend on padding or wrapped mask operations.
edge_x = 5
edge_y = H - HOLE_H - 4
edge_bg, edge_piece = make_pair(dim=0.35, hole_x=edge_x, hole_y=edge_y)
gap = find_gap_left(edge_bg, edge_piece, HOLE_W, HOLE_H)
assert abs(gap - edge_x) <= 3, f"boundary hole at {edge_x}, detector said {gap}"
print(f"ok: near-boundary hole found at x={gap}")

# Decode and alpha failures should be explicit rather than producing guesses.
invalid_dir = Path(tempfile.mkdtemp())
invalid_piece = invalid_dir / "invalid-piece.webp"
invalid_piece.write_bytes(b"not an image")
try:
    find_gap_left(bg, invalid_piece, HOLE_W, HOLE_H)
except GapNotFoundError:
    print("ok: unreadable piece raises GapNotFoundError")
else:
    raise AssertionError("unreadable piece did not raise GapNotFoundError")

transparent_piece = invalid_dir / "transparent-piece.png"
assert cv2.imwrite(str(transparent_piece), np.zeros((68, 68, 4), np.uint8))
try:
    find_gap_left(bg, transparent_piece, HOLE_W, HOLE_H)
except GapNotFoundError:
    print("ok: empty alpha mask raises GapNotFoundError")
else:
    raise AssertionError("empty alpha mask did not raise GapNotFoundError")


class FakeActions:
    def __init__(self) -> None:
        self.target = None
        self.held = False
        self.released = False
        self.x = 0.0
        self.y = 0.0

    def move_to(self, target, duration: float):
        self.target = target
        return self

    def hold(self):
        self.held = True
        return self

    def move(self, dx: float, dy: float, duration: float):
        self.x += dx
        self.y += dy
        self.target.rect.x += dx
        self.target.rect.y += dy
        return self

    def wait(self, duration: float):
        return self

    def release(self):
        self.released = True
        return self


class FakeTab:
    def __init__(self) -> None:
        self.actions = FakeActions()


class FakeRect:
    def __init__(self) -> None:
        self.x = 10.0
        self.y = 20.0

    @property
    def location(self) -> tuple[float, float]:
        return self.x, self.y


class FakeHandle:
    def __init__(self) -> None:
        self.rect = FakeRect()


fake_tab = FakeTab()
fake_handle = FakeHandle()
actual_distance = drag_slider(fake_tab, fake_handle, 137.5, duration=0)
assert fake_tab.actions.target is fake_handle
assert fake_tab.actions.held and fake_tab.actions.released
assert abs(fake_tab.actions.x - 137.5) < 1e-9
assert abs(fake_tab.actions.y) < 1e-9
assert abs(actual_distance - 137.5) < 1e-9
print("ok: drag presses the handle and ends at the exact requested offset")
