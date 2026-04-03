import logging
import os
from typing import Callable

from PIL import Image, ImageGrab

try:
    import dxcam
except Exception:
    dxcam = None

try:
    import mss
except ImportError:
    mss = None

import windows_mcp.uia as uia

logger = logging.getLogger(__name__)


def _build_crop_box(capture_rect: uia.Rect, padding: int = 0) -> tuple[int, int, int, int]:
    left_offset, top_offset, _, _ = uia.GetVirtualScreenRect()
    return (
        capture_rect.left - left_offset + padding,
        capture_rect.top - top_offset + padding,
        capture_rect.right - left_offset + padding,
        capture_rect.bottom - top_offset + padding,
    )


def _crop_screenshot(
        screenshot: Image.Image, capture_rect: uia.Rect | None
) -> Image.Image:
    if capture_rect is None:
        return screenshot
    return screenshot.crop(_build_crop_box(capture_rect))


_DXCAM_CAMERA_CACHE: dict[int, object] = {}


def get_screenshot_backend() -> str:
    value = os.getenv("WINDOWS_MCP_SCREENSHOT_BACKEND", "auto")
    normalized = value.strip().lower()
    if normalized in {"auto", "pillow", "dxcam", "mss"}:
        return normalized
    logger.warning(
        "Unknown screenshot backend '%s'; falling back to auto",
        value,
    )
    return "auto"


def resolve_dxcam_region(
        capture_rect,
        get_monitors_rect: Callable[[], list],
) -> tuple[int, tuple[int, int, int, int] | None] | None:
    if capture_rect is None:
        return 0, None

    monitor_rects = get_monitors_rect()
    for output_idx, monitor_rect in enumerate(monitor_rects):
        if (
                monitor_rect.left <= capture_rect.left
                and monitor_rect.top <= capture_rect.top
                and monitor_rect.right >= capture_rect.right
                and monitor_rect.bottom >= capture_rect.bottom
        ):
            if monitor_rect == capture_rect:
                return output_idx, None
            return output_idx, (
                capture_rect.left - monitor_rect.left,
                capture_rect.top - monitor_rect.top,
                capture_rect.right - monitor_rect.left,
                capture_rect.bottom - monitor_rect.top,
            )
    return None


def get_dxcam_camera(output_idx: int):
    global _DXCAM_CAMERA_CACHE

    if dxcam is None:
        raise RuntimeError("dxcam is not available")

    camera = _DXCAM_CAMERA_CACHE.get(output_idx)
    if camera is None:
        camera = dxcam.create(output_idx=output_idx, processor_backend="numpy")
        _DXCAM_CAMERA_CACHE[output_idx] = camera
    return camera


def capture_with_dxcam(
        capture_rect,
        get_monitors_rect: Callable[[], list],
) -> Image.Image:
    resolved = resolve_dxcam_region(capture_rect, get_monitors_rect)
    if resolved is None:
        raise ValueError("DXGI capture supports only regions fully contained within one display")

    output_idx, region = resolved
    camera = get_dxcam_camera(output_idx)
    frame = camera.grab(region=region, copy=True, new_frame_only=False)
    if frame is None:
        raise RuntimeError("DXGI capture returned no frame")
    return Image.fromarray(frame)


def capture_with_pillow(capture_rect) -> Image.Image:
    grab_kwargs = {"all_screens": True}
    if capture_rect is not None:
        grab_kwargs["bbox"] = (
            capture_rect.left,
            capture_rect.top,
            capture_rect.right,
            capture_rect.bottom,
        )
    try:
        screenshot = ImageGrab.grab(**grab_kwargs)
    except (OSError, RuntimeError, ValueError):
        if capture_rect is not None:
            logger.warning(
                "Failed to capture selected region directly, falling back to virtual screen crop"
            )
            return _crop_screenshot(ImageGrab.grab(all_screens=True), capture_rect)
        logger.warning("Failed to capture virtual screen, using primary screen")
        screenshot = ImageGrab.grab()
    return _crop_screenshot(screenshot, capture_rect)


def capture_with_mss(capture_rect) -> Image.Image:
    if mss is None:
        raise RuntimeError("mss is not available")

    with mss.mss() as sct:
        if capture_rect is None:
            monitor = sct.monitors[0]
        else:
            monitor = {
                "left": capture_rect.left,
                "top": capture_rect.top,
                "width": capture_rect.right - capture_rect.left,
                "height": capture_rect.bottom - capture_rect.top,
            }
        raw = sct.grab(monitor)
        image = Image.frombytes("RGB", raw.size, raw.rgb)
    return _crop_screenshot(image, capture_rect)


def _auto_backend_chain() -> list[str]:
    return ["dxcam", "mss", "pillow"]


def capture(
        capture_rect,
        get_monitors_rect: Callable[[], list],
        backend: str | None = None,
) -> tuple[Image.Image, str]:
    selected = backend or get_screenshot_backend()
    chain = _auto_backend_chain() if selected == "auto" else [selected]

    for backend_name in chain:
        try:
            if backend_name == "dxcam":
                if capture_rect is None:
                    continue
                if dxcam is None:
                    continue
                return (
                    capture_with_dxcam(
                        capture_rect,
                        get_monitors_rect,
                    ),
                    "dxcam",
                )

            if backend_name == "mss":
                if mss is None:
                    continue
                return (
                    capture_with_mss(capture_rect),
                    "mss",
                )

            if backend_name == "pillow":
                return capture_with_pillow(capture_rect), "pillow"

        except (OSError, RuntimeError, ValueError):
            logger.warning(
                "Screenshot backend '%s' failed; trying next backend",
                backend_name,
                exc_info=selected != "auto",
            )

    # Final safety fallback so capture always returns an image on supported hosts.
    return capture_with_pillow(capture_rect), "pillow"

