"""Structural rigid-motion check for supervised homepage -> Messages only.

Not a button/recipient classifier and never an approval to click. The caller must
establish authorization by human target review or the separate preauthorized,
qualified Messages-slot policy, and recheck before dispatch.
Sending/input/general navigation keep the original, unregistered pixel guard.
"""
from io import BytesIO

from douyin_policy import RegionChanged, require_same_context, require_same_regions, validate_action

REVIEW_RADIUS = 32  # The existing preview circle, confirmed INSIDE the button.
MAX_SHIFT = REVIEW_RADIUS // 4  # At most a quarter of the reviewed radius per axis.
SEARCH_RADIUS = MAX_SHIFT + 4  # Look farther only to REJECT an out-of-bound match.
MIN_CORRELATION = .98
MIN_SCORE_GAP = .002  # Ambiguous registrations fail closed.
MAX_MEAN_DELTA = 8
CONTRAST_RANGE = (.85, 1.15)
ANCHORS = ((24, 2270, 400, 2388), (890, 2270, 1056, 2388))


def structural_match(left, right, left_stats):
    """RGB normalized correlation; tolerate small brightness/encoding changes.

    Pearson correlation is computed from difference RMS, not an image model.
    Every channel must have texture and retain brightness/contrast. No masking.
    """
    from PIL import ImageChops, ImageStat
    right_stats = ImageStat.Stat(right)
    difference = ImageStat.Stat(ImageChops.difference(left, right))
    correlations, deltas, ratios = [], [], []
    for channel in range(3):
        std_left, std_right = left_stats.stddev[channel], right_stats.stddev[channel]
        if min(std_left, std_right) < 10:
            return None
        delta = left_stats.mean[channel] - right_stats.mean[channel]
        ratio = std_right / std_left
        if abs(delta) > MAX_MEAN_DELTA or not CONTRAST_RANGE[0] <= ratio <= CONTRAST_RANGE[1]:
            return None
        correlation = (std_left**2 + std_right**2 + delta**2 - difference.rms[channel]**2) / (2 * std_left * std_right)
        correlation = max(-1.0, min(1.0, correlation))
        if correlation < MIN_CORRELATION:
            return None
        correlations.append(correlation)
        deltas.append(abs(delta))
        ratios.append(ratio)
    return {"correlation_rgb": correlations, "mean_delta_rgb": deltas,
            "contrast_ratio_rgb": ratios, "mean_absolute_difference_rgb": difference.mean}


def require_same_messages_regions(before, after, action, *, before_context, after_context):
    """Return alignment evidence or None for an original strict-check pass.

    Require an unambiguous, bounded common translation of target AND both anchors.
    Structural matching is only continuity evidence for an authorized candidate,
    not permission to click or proof of its semantics. The coordinate stays fixed.
    """
    validate_action(action)
    if (set(action) != {"_metadata", "action", "element"}
            or action.get("_metadata") != "do" or action.get("action") != "Tap"):
        raise RuntimeError("微移检查只用于人工核对的底部消息候选Tap。")
    x, y = action["element"]
    if not (550 <= x <= 850 and 900 <= y < 1000):
        raise RuntimeError("消息候选坐标不在本机底部范围，不启用微移检查。")
    native_y = int(y * 2400 / 1000)
    if native_y - REVIEW_RADIUS < 2250 or native_y + REVIEW_RADIUS >= 2400:
        raise RuntimeError("消息候选的完整预览圆未落在本机底部导航带，不批准点击。")
    require_same_context(before_context, after_context)
    try:
        require_same_regions(before, after, action,
                             before_context=before_context, after_context=after_context)
        return None
    except RegionChanged as original:
        from PIL import Image, ImageStat
        with Image.open(BytesIO(before)) as source, Image.open(BytesIO(after)) as target:
            # Size/context failures have already propagated from the strict guard.
            left, right = source.convert("RGB"), target.convert("RGB")
            boxes = (tuple(original.evidence["box"]), *ANCHORS)
            crops = [left.crop(box) for box in boxes]
            stats = [ImageStat.Stat(crop) for crop in crops]
            # Blank/low-detail strips cannot establish registration.
            if any(min(stat.stddev) < 10 for stat in stats):
                raise
            matches = []
            for dx in range(-SEARCH_RADIUS, SEARCH_RADIUS + 1):
                for dy in range(-SEARCH_RADIUS, SEARCH_RADIUS + 1):
                    regions = []
                    for box, crop, stat in zip(boxes, crops, stats):
                        shifted = (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)
                        if (shifted[0] < 0 or shifted[1] < 0
                                or shifted[2] > right.width or shifted[3] > right.height):
                            break
                        candidate = right.crop(shifted)
                        evidence = structural_match(crop, candidate, stat)
                        if evidence is None:
                            break
                        regions.append(evidence)
                    if len(regions) == len(boxes):
                        matches.append({"translation_native_px": [dx, dy],
                                        "regions": regions,
                                        "mean_correlation": sum(sum(region["correlation_rgb"]) for region in regions) / 9})
            matches.sort(key=lambda match: match["mean_correlation"], reverse=True)
            if (not matches or max(abs(v) for v in matches[0]["translation_native_px"]) > MAX_SHIFT
                    or (len(matches) > 1 and matches[0]["mean_correlation"] - matches[1]["mean_correlation"] < MIN_SCORE_GAP)):
                raise
            return {"check": "messages_structure_v2", "max_shift_native_px": MAX_SHIFT,
                    "review_radius_native_px": REVIEW_RADIUS,
                    "min_correlation_rgb": MIN_CORRELATION, "max_mean_delta_rgb": MAX_MEAN_DELTA,
                    "contrast_ratio_range": list(CONTRAST_RANGE),
                    "search_radius_native_px": SEARCH_RADIUS, "min_score_gap": MIN_SCORE_GAP,
                    "boxes": [list(box) for box in boxes], "original_difference": original.evidence,
                    **matches[0], "coordinates_rewritten": False, "semantic_proof": False}
