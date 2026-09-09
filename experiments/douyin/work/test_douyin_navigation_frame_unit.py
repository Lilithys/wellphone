"""Synthetic navigation-motion regressions; no private screenshots or phone."""
from io import BytesIO
import unittest

from PIL import Image, ImageDraw

from douyin_navigation_frame import MAX_SHIFT, NAVIGATION_TOP, SEARCH_RADIUS, require_same_messages_regions
from douyin_policy import ContextChanged, RegionChanged, require_same_regions
from test_douyin_unit import CONTEXT

TAP = {"_metadata": "do", "action": "Tap", "element": [696, 965]}


def navigation_frame(shift=(0, 0), *, target_shift=None, target_missing=False,
                     anchors_missing=False, changed_content=False, changed_badge=False,
                     brightness_delta=0, contrast=1, video_bottom=1850):
    picture = Image.new("RGB", (1080, 2400), (28, 28, 28))
    draw = ImageDraw.Draw(picture)
    # Deliberately not a real app screenshot: independent, asymmetric glyphs.
    for center, missing, offset in ((170, anchors_missing, shift),
                                    (751, target_missing, target_shift or shift),
                                    (970, anchors_missing, shift)):
        if missing:
            continue
        dx, dy = offset
        for column in range(4):
            xx = center - 40 + column * 23 + dx
            draw.rectangle((xx, 2290 + dy, xx + 9, 2348 - column * 4 + dy), fill="white")
            draw.rectangle((xx, 2295 + column * 8 + dy, xx + 18, 2301 + column * 8 + dy), fill="white")
    if not target_missing:
        dx, dy = target_shift or shift
        draw.rectangle((785 + dx, 2270 + dy, 810 + dx, 2293 + dy),
                       fill="blue" if changed_badge else "red")
    draw.rectangle((120, 500, 930, video_bottom), fill="blue" if changed_content else "gray")
    if brightness_delta or contrast != 1:
        picture = picture.point(lambda value: max(0, min(255, round(value * contrast + brightness_delta))))
    out = BytesIO()
    picture.save(out, format="PNG")
    return out.getvalue()


class NavigationFrameTests(unittest.TestCase):
    def check(self, before, after, action=TAP, context=CONTEXT):
        return require_same_messages_regions(before, after, action,
            before_context=CONTEXT, after_context=context)

    def test_original_strict_pass_has_no_alignment_evidence(self):
        self.assertIsNone(self.check(navigation_frame(), navigation_frame()))

    def test_reported_minus_two_shift_and_inverse_are_uniquely_registered(self):
        for shift in ((-2, -2), (2, 2), (0, -2), (-1, 1)):
            before, after = navigation_frame(), navigation_frame(shift)
            with self.assertRaises(RegionChanged):
                require_same_regions(before, after, TAP)
            evidence = self.check(before, after)
            self.assertEqual(evidence["translation_native_px"], list(shift))
            self.assertFalse(evidence["coordinates_rewritten"])
            self.assertFalse(evidence["semantic_proof"])
            self.assertEqual(evidence["check"], "messages_structure_v3")
            self.assertEqual(len(evidence["regions"]), 3)

    def test_video_change_is_not_nav_motion(self):
        self.assertIsNotNone(self.check(navigation_frame(), navigation_frame((-2, -2), changed_content=True)))

    def test_click_box_overlapping_video_does_not_reject_stable_navigation(self):
        action = {**TAP, "element": [696, 961]}
        before = navigation_frame(video_bottom=NAVIGATION_TOP - 1)
        after = navigation_frame((-1, -1), changed_content=True, video_bottom=NAVIGATION_TOP - 1)
        with self.assertRaises(RegionChanged):
            require_same_regions(before, after, action)
        evidence = self.check(before, after, action=action)
        self.assertEqual(evidence["original_difference"]["box"][1], 2242)
        self.assertEqual(evidence["translation_native_px"], [-1, -1])
        self.assertGreaterEqual(evidence["boxes"][0][1] - SEARCH_RADIUS, NAVIGATION_TOP)
        self.assertEqual(action["element"], [696, 961])

    def test_video_overlap_fix_still_rejects_changed_badge_or_missing_target(self):
        action = {**TAP, "element": [696, 961]}
        before = navigation_frame(video_bottom=NAVIGATION_TOP - 1)
        for change in ({"changed_badge": True}, {"target_missing": True}, {"anchors_missing": True}):
            with self.subTest(change=change), self.assertRaises(RegionChanged):
                self.check(before, navigation_frame((-1, -1), changed_content=True,
                           video_bottom=NAVIGATION_TOP - 1, **change), action=action)

    def test_three_pixels_and_small_brightness_change_preserve_structure(self):
        evidence = self.check(navigation_frame(), navigation_frame((-3, -3), brightness_delta=5))
        self.assertEqual(evidence["translation_native_px"], [-3, -3])
        self.assertTrue(all(min(region["correlation_rgb"]) >= .98 for region in evidence["regions"]))

    def test_bounded_motion_within_review_clearance(self):
        for shift in ((-MAX_SHIFT, -MAX_SHIFT), (MAX_SHIFT, 0)):
            evidence = self.check(navigation_frame(), navigation_frame(shift))
            self.assertEqual(evidence["translation_native_px"], list(shift))

    def test_shift_beyond_review_clearance_is_not_accepted(self):
        for shift in ((-MAX_SHIFT - 1, -2), (-2, MAX_SHIFT + 1), (20, 0)):
            with self.assertRaises(RegionChanged):
                self.check(navigation_frame(), navigation_frame(shift))

    def test_large_brightness_change_or_fade_is_rejected(self):
        for kwargs in ({"brightness_delta": 30}, {"contrast": .5}):
            with self.assertRaises(RegionChanged):
                self.check(navigation_frame(), navigation_frame((-3, -3), **kwargs))

    def test_zero_motion_with_small_brightness_change(self):
        evidence = self.check(navigation_frame(), navigation_frame(brightness_delta=5))
        self.assertEqual(evidence["translation_native_px"], [0, 0])

    def test_target_motion_without_common_anchor_motion_is_rejected(self):
        with self.assertRaises(RegionChanged):
            self.check(navigation_frame(), navigation_frame(target_shift=(-2, -2)))

    def test_independent_target_and_anchor_motion_is_rejected(self):
        with self.assertRaises(RegionChanged):
            self.check(navigation_frame(), navigation_frame((-2, -2), target_shift=(2, 2)))

    def test_disappearing_target_or_anchors_are_rejected(self):
        for kwargs in ({"target_missing": True}, {"anchors_missing": True}):
            with self.assertRaises(RegionChanged):
                self.check(navigation_frame(), navigation_frame((-2, -2), **kwargs))

    def test_blank_anchors_do_not_establish_registration(self):
        with self.assertRaises(RegionChanged):
            self.check(navigation_frame(anchors_missing=True), navigation_frame((-2, -2), anchors_missing=True))

    def test_periodic_ambiguous_registration_is_rejected(self):
        frames = []
        for phase in (0, 1):
            picture = Image.new("RGB", (1080, 2400), "black")
            draw = ImageDraw.Draw(picture)
            for x in range(phase, 1080, 4):
                draw.rectangle((x, 0, x + 1, 2399), fill="white")
            out = BytesIO()
            picture.save(out, format="PNG")
            frames.append(out.getvalue())
        with self.assertRaises(RegionChanged):
            self.check(*frames)

    def test_badge_change_is_not_masked_as_video(self):
        with self.assertRaises(RegionChanged):
            self.check(navigation_frame(), navigation_frame((-2, -2), changed_badge=True))

    def test_changed_activity_display_or_other_app_never_reaches_alignment(self):
        for context in ({**CONTEXT, "display_id": 52}, {**CONTEXT, "activity": "com.ss.android.ugc.aweme/.Other"}):
            with self.assertRaises(ContextChanged):
                self.check(navigation_frame(), navigation_frame((-2, -2)), context=context)
        for context in ({**CONTEXT, "display_id": 0}, {**CONTEXT, "activity": "other.app/.Screen"}, None):
            with self.assertRaises(RuntimeError):
                self.check(navigation_frame(), navigation_frame((-2, -2)), context=context)

    def test_wrong_action_scope_and_coordinates_rejected(self):
        for action in ({**TAP, "message": "SEND_ONE"}, {**TAP, "element": [99, 965]},
                       {**TAP, "element": [696, 500]}, {"_metadata": "do", "action": "Back"},
                       {**TAP, "element": [696, 940]}, {**TAP, "element": [696, 990]},
                       {"_metadata": "do", "action": "Type", "text": "1"}):
            with self.assertRaises(RuntimeError):
                self.check(navigation_frame(), navigation_frame((-2, -2)), action=action)

    def test_corrupt_or_wrong_dimensions_do_not_fall_back(self):
        out = BytesIO()
        Image.new("RGB", (540, 1200)).save(out, format="PNG")
        for bad in (b"broken PNG", out.getvalue()):
            with self.assertRaises((RuntimeError, OSError)):
                self.check(navigation_frame(), bad)

    def test_send_guard_remains_strict_on_same_pair(self):
        with self.assertRaises(RegionChanged):
            require_same_regions(navigation_frame(), navigation_frame((-2, -2)), TAP, sending=True)


if __name__ == "__main__":
    unittest.main()
