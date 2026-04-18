import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pointer_host_logic import (
    apply_deadzone,
    bbox_iou,
    center_distance_ratio,
    hold_angle_if_within_threshold,
    map_center_to_angle,
    match_target_bbox,
    resolve_angle_step,
    should_send_angle,
    should_stop_for_loss,
    smooth_angle,
    smooth_angle_adaptive,
    smooth_center,
)


class PointerHostLogicTests(unittest.TestCase):
    def test_bbox_iou_reports_overlap(self) -> None:
        self.assertAlmostEqual(bbox_iou((10, 10, 20, 20), (20, 20, 20, 20)), 1 / 7)

    def test_center_distance_ratio_grows_with_farther_targets(self) -> None:
        near_ratio = center_distance_ratio((100, 100, 50, 100), (110, 105, 50, 100))
        far_ratio = center_distance_ratio((100, 100, 50, 100), (220, 100, 50, 100))
        self.assertLess(near_ratio, far_ratio)

    def test_match_target_bbox_selects_closest_valid_candidate(self) -> None:
        previous_bbox = (100, 100, 50, 100)
        candidates = [
            (105, 102, 50, 100),
            (220, 100, 50, 100),
        ]
        match = match_target_bbox(
            previous_bbox,
            candidates,
            min_iou=0.0,
            max_center_ratio=2.0,
            max_area_change=1.0,
        )
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.index, 0)

    def test_match_target_bbox_rejects_large_area_change(self) -> None:
        previous_bbox = (100, 100, 50, 100)
        candidates = [(105, 102, 120, 240)]
        match = match_target_bbox(
            previous_bbox,
            candidates,
            min_iou=0.0,
            max_center_ratio=2.0,
            max_area_change=0.5,
        )
        self.assertIsNone(match)

    def test_match_target_bbox_rejects_low_iou_when_required(self) -> None:
        previous_bbox = (100, 100, 50, 100)
        candidates = [(160, 100, 50, 100)]
        match = match_target_bbox(
            previous_bbox,
            candidates,
            min_iou=0.2,
            max_center_ratio=2.0,
            max_area_change=1.0,
        )
        self.assertIsNone(match)

    def test_smooth_center_interpolates_coordinates(self) -> None:
        center = smooth_center((100.0, 80.0), (140.0, 120.0), 0.25)
        self.assertEqual(center, (110.0, 90.0))

    def test_map_center_to_angle_maps_left_center_right(self) -> None:
        self.assertEqual(map_center_to_angle(0, 640, 20, 90, 160), 20)
        self.assertEqual(map_center_to_angle(320, 640, 20, 90, 160), 90)
        self.assertEqual(map_center_to_angle(640, 640, 20, 90, 160), 160)

    def test_apply_deadzone_snaps_to_center(self) -> None:
        self.assertEqual(apply_deadzone(91, 90, 2), 90)
        self.assertEqual(apply_deadzone(95, 90, 2), 95)

    def test_hold_angle_if_within_threshold_absorbs_small_jitter(self) -> None:
        self.assertEqual(hold_angle_if_within_threshold(90, 92, 2), 90)
        self.assertEqual(hold_angle_if_within_threshold(90, 93, 2), 93)

    def test_smooth_angle_limits_step_size(self) -> None:
        self.assertEqual(smooth_angle(None, 120, 4), 120)
        self.assertEqual(smooth_angle(90, 100, 4), 94)
        self.assertEqual(smooth_angle(90, 87, 4), 87)

    def test_resolve_angle_step_uses_three_error_bands(self) -> None:
        self.assertEqual(resolve_angle_step(90, 92, 4, 18, 1, 3, 6), 1)
        self.assertEqual(resolve_angle_step(90, 100, 4, 18, 1, 3, 6), 3)
        self.assertEqual(resolve_angle_step(90, 20, 4, 18, 1, 3, 6), 6)

    def test_smooth_angle_adaptive_avoids_initial_jump_from_center(self) -> None:
        self.assertEqual(
            smooth_angle_adaptive(
                None,
                20,
                90,
                small_error_threshold=4,
                medium_error_threshold=18,
                small_step=1,
                medium_step=3,
                large_step=6,
            ),
            84,
        )

    def test_smooth_angle_adaptive_uses_small_step_near_target(self) -> None:
        self.assertEqual(
            smooth_angle_adaptive(
                12,
                10,
                90,
                small_error_threshold=4,
                medium_error_threshold=18,
                small_step=1,
                medium_step=3,
                large_step=6,
            ),
            11,
        )

    def test_smooth_angle_adaptive_stays_monotonic_toward_target(self) -> None:
        self.assertEqual(
            smooth_angle_adaptive(
                90,
                160,
                90,
                small_error_threshold=4,
                medium_error_threshold=18,
                small_step=1,
                medium_step=3,
                large_step=6,
            ),
            96,
        )

    def test_should_send_angle_respects_threshold(self) -> None:
        self.assertTrue(should_send_angle(None, 90, 2))
        self.assertFalse(should_send_angle(90, 91, 2))
        self.assertTrue(should_send_angle(90, 93, 2))

    def test_should_stop_for_loss_holds_until_threshold(self) -> None:
        self.assertFalse(should_stop_for_loss(4, 5))
        self.assertTrue(should_stop_for_loss(5, 5))


if __name__ == "__main__":
    unittest.main()
