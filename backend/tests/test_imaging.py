import cv2
import numpy as np
import pytest

from app.imaging import suitability as s


def enc(img):
    return cv2.imencode(".jpg", img)[1].tobytes()


def textured(h=600, w=800, base=140, seed=0):
    rng = np.random.default_rng(seed)
    img = cv2.add(np.full((h, w, 3), base, np.uint8), rng.integers(0, 60, (h, w, 3), dtype=np.uint8))
    for _ in range(80):
        cv2.circle(img, (int(rng.integers(0, w)), int(rng.integers(0, h))), int(rng.integers(5, 40)),
                   [int(c) for c in rng.integers(0, 255, 3)], -1)
    return img


def test_garbage_rejected():
    r = s.assess(b"not an image", "face")
    assert r.verdict == "rejected" and r.reason == "invalid_image"


def test_blurry_rejected():
    img = cv2.GaussianBlur(textured(), (0, 0), 25)         # blurred but normally exposed
    assert s.assess(enc(img), "face").reason == "blurry"


def test_dark_rejected():
    img = (textured() * 0.15).astype(np.uint8)
    assert s.assess(enc(img), "face").reason in ("too_dark", "blurry")


def test_small_rejected():
    assert s.assess(enc(textured(100, 100)), "body").reason == "too_small"


def test_no_face_in_noise():
    assert s.assess(enc(textured()), "face").reason == "no_face"


def test_noise_not_accepted_as_scalp_without_warning():
    r = s.assess(enc(textured()), "hair_scalp")
    assert r.verdict != "accepted" or r.warnings


def test_unknown_region():
    with pytest.raises(ValueError):
        s.assess(b"", "knee")


def smooth_skin_like(h=600, w=800, seed=2):
    """In-focus but low-texture, like a skin close-up: smooth colour field with mild sensor noise."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = 150 + 32 * np.sin(xx / 170.0) + 26 * np.cos(yy / 130.0)      # contrast std ~ 22, like real close-ups (median 25)
    img = np.stack([base + 14, base - 6, base - 22], -1) + rng.normal(0, 1.4, (h, w, 3))
    return np.clip(img, 0, 255).astype(np.uint8)


def test_skin_profile_accepts_smooth_closeups_that_the_face_profile_would_call_blurry():
    from app.imaging import suitability as s
    img = smooth_skin_like()
    q = s.quality_metrics(img)
    assert "blurry" in s.quality_issues(q, "default") or "low_contrast" in s.quality_issues(q, "default")    # the old rule would reject it
    assert s.quality_issues(q, "skin_closeup") == []                                                     # calibrated rule accepts a usable close-up
    r = s.assess(cv2.imencode(".png", img)[1].tobytes(), "body")      # PNG: JPEG would smooth away the synthetic sensor noise
    assert r.reason not in ("blurry", "low_contrast", "too_small")


def test_skin_profile_still_rejects_real_degradations():
    from app.imaging import suitability as s
    img = smooth_skin_like()
    for name, bad in {"blur": cv2.GaussianBlur(textured(), (0, 0), 25), "dark": (img * 0.3).astype(np.uint8),
                      "washed": np.clip((img.astype(np.float32) - img.mean()) * 0.1 + img.mean() + 30, 0, 255).astype(np.uint8)}.items():
        assert s.quality_issues(s.quality_metrics(bad), "skin_closeup"), name
    assert s.assess(enc((img * 0.3).astype(np.uint8)), "other").reason == "too_dark"
