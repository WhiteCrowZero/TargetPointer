import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pointer_host_logic import apply_deadzone, map_center_to_angle, should_send_angle, should_stop_for_loss, smooth_angle


class PointerHostLogicTests(unittest.TestCase):
    def test_map_center_to_angle_maps_left_center_right(self) -> None:
        self.assertEqual(map_center_to_angle(0, 640, 20, 90, 160), 20)
        self.assertEqual(map_center_to_angle(320, 640, 20, 90, 160), 90)
        self.assertEqual(map_center_to_angle(640, 640, 20, 90, 160), 160)

    def test_apply_deadzone_snaps_to_center(self) -> None:
        self.assertEqual(apply_deadzone(91, 90, 2), 90)
        self.assertEqual(apply_deadzone(95, 90, 2), 95)

    def test_smooth_angle_limits_step_size(self) -> None:
        self.assertEqual(smooth_angle(None, 120, 4), 120)
        self.assertEqual(smooth_angle(90, 100, 4), 94)
        self.assertEqual(smooth_angle(90, 87, 4), 87)

    def test_should_send_angle_respects_threshold(self) -> None:
        self.assertTrue(should_send_angle(None, 90, 2))
        self.assertFalse(should_send_angle(90, 91, 2))
        self.assertTrue(should_send_angle(90, 93, 2))

    def test_should_stop_for_loss_holds_until_threshold(self) -> None:
        self.assertFalse(should_stop_for_loss(4, 5))
        self.assertTrue(should_stop_for_loss(5, 5))


if __name__ == "__main__":
    unittest.main()
