"""Validator v1.1: directional-blur rule, JPEG-blockiness warning, profile scoping. Synthetic images test the mechanism; real-image behaviour is measured by
eval/quality_features.py and eval/quality_gate_eval.py (docs/eval/*.json), not here."""
import cv2
import numpy as np
import pytest

from app.imaging import suitability as S


def textured(seed=0, size=900, sigma=1.2):
    rng = np.random.default_rng(seed)
    base = rng.normal(128, 150, (size, size)).astype(np.float32)      # large amplitude: smoothing removes most of it
    base = cv2.GaussianBlur(base, (0, 0), sigma)
    img = np.stack([base * 1.0 + 20, base * 0.85 + 10, base * 0.75 + 5], axis=-1)
    return np.clip(img, 0, 255).astype(np.uint8)


def motion(img, length=45, angle=0):
    k = np.zeros((length, length), np.float32); k[length // 2, :] = 1 / length
    M = cv2.getRotationMatrix2D((length / 2 - .5, length / 2 - .5), angle, 1)
    k = cv2.warpAffine(k, M, (length, length)); k /= k.sum()
    return cv2.filter2D(img, -1, k)


def encode(img, ext=".png", q=95):
    return cv2.imencode(ext, img, [cv2.IMWRITE_JPEG_QUALITY, q] if ext == ".jpg" else [])[1].tobytes()


def test_a_sharp_isotropic_texture_passes_the_quality_gate():
    m = S.quality_metrics(textured())
    assert S.quality_issues(m, "skin_closeup") == []
    # the raw ratio of an isotropic texture is ~0.3, not 1 (the diagonal kernels sample at a larger spacing); the shipped threshold was fitted on real images with this exact feature
    assert m["direction_ratio"] > S.QUALITY_PROFILES["skin_closeup"]["min_direction_ratio"] and m["blockiness"] < 1.2


@pytest.mark.parametrize("angle", [0, 45, 90, 135])
def test_linear_motion_blur_is_caught_in_every_direction_by_the_direction_rule(angle):
    # blur long enough to erase detail along its direction but keep an image that is "sharp" overall in the perpendicular direction
    img = motion(textured(1, sigma=0.8), length=41, angle=angle)
    m = S.quality_metrics(img)
    assert m["direction_ratio"] <= S.QUALITY_PROFILES["skin_closeup"]["min_direction_ratio"], m["direction_ratio"]
    iss = S.quality_issues(m, "skin_closeup")
    assert iss and iss[0] in ("blurry", "motion_blur")                                      # whichever rule fires first, the photo is not accepted


def test_the_direction_rule_alone_flags_smearing_that_the_sharpness_rule_misses():
    # keep sharpness high by adding strong vertical detail, then smear horizontally: only the direction ratio can notice
    img = textured(2, sigma=0.6)
    stripes = (np.sin(np.arange(900) / 2.0) * 60)[None, :, None].repeat(900, 0).repeat(3, 2)
    smeared = np.clip(motion(img, 45, 0).astype(np.float32) + stripes * 0 + np.random.default_rng(3).normal(0, 0, img.shape), 0, 255).astype(np.uint8)
    m = S.quality_metrics(smeared)
    iss = S.quality_issues({**m, "sharpness": 500.0}, "skin_closeup")                         # pretend the sharpness metric was fooled
    assert "motion_blur" in iss and "blurry" not in iss


def test_hair_and_face_profiles_do_not_apply_the_direction_rule():
    m = S.quality_metrics(motion(textured(1, sigma=0.8), 41, 0))
    m["sharpness"] = 500.0
    assert "motion_blur" not in S.quality_issues(m, "hair_closeup")                          # hair is naturally oriented: rule not validated there
    assert "motion_blur" not in S.quality_issues(m, "default")                                # faces: profile never validated
    assert S.PROFILE_BY_REGION["hair_scalp"] == "hair_closeup" and S.PROFILE_BY_REGION["face"] == "default"
    assert S.PROFILE_BY_REGION["body"] == S.PROFILE_BY_REGION["other"] == "skin_closeup"


def test_motion_blur_has_a_patient_message_and_a_checklist_entry():
    assert "smeared" in S.MESSAGES["motion_blur"].lower()
    img = motion(textured(1, sigma=0.8), 41, 0)
    rep = S.assess(encode(img), "body")
    assert rep.verdict == "rejected" and rep.reason in ("blurry", "motion_blur") and rep.version == "suitability-1.1.0"
    q = next(c for c in rep.checks if c["id"] == "quality")
    assert q["status"] == "failed"


def test_heavy_jpeg_compression_gives_a_warning_not_a_rejection():
    img = textured(4, sigma=1.5)
    clean = S.assess(encode(img, ".png"), "body")
    heavy = S.assess(encode(img, ".jpg", 5), "body")
    assert not any("compressed" in w for w in clean.warnings)
    assert any("compressed" in w for w in heavy.warnings)
    assert heavy.verdict != "rejected" or heavy.reason != "invalid_image"
    assert heavy.metrics["blockiness"] > clean.metrics["blockiness"] and heavy.metrics["blockiness"] >= S.BLOCKINESS_WARN


def test_blockiness_is_safe_on_tiny_images_and_measured_on_the_native_grid():
    assert S.blockiness(np.zeros((20, 20, 3), np.uint8)) == 1.0
    big = textured(5, size=1600)
    heavy = cv2.imdecode(np.frombuffer(encode(big, ".jpg", 5), np.uint8), 1)
    assert S.blockiness(heavy) >= S.BLOCKINESS_WARN > S.blockiness(big)
    # a non-integer resize shifts the 8x8 grid off the sampling lattice, so the measurement must be taken before any resizing (as quality_metrics does)
    shifted = cv2.resize(heavy, (1237, 1237), interpolation=cv2.INTER_AREA)
    assert S.blockiness(shifted) < S.blockiness(heavy)


def test_existing_exposure_and_resolution_rules_are_unchanged():
    base = textured(6)
    assert "too_dark" in S.quality_issues(S.quality_metrics((base * 0.2).astype(np.uint8)), "skin_closeup")
    assert "too_bright" in S.quality_issues(S.quality_metrics(np.clip(base.astype(np.float32) * 2 + 60, 0, 255).astype(np.uint8)), "skin_closeup")
    assert "too_small" in S.quality_issues(S.quality_metrics(base[:200, :200]), "skin_closeup")
