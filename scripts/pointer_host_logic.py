def map_center_to_angle(center_x: float, frame_width: int, min_angle: int, center_angle: int, max_angle: int) -> int:
    if frame_width <= 0:
        raise ValueError("frame_width must be positive")

    half_width = frame_width / 2.0
    offset = center_x - half_width
    ratio = max(-1.0, min(1.0, offset / half_width))
    half_span = min(center_angle - min_angle, max_angle - center_angle)
    mapped = center_angle + ratio * half_span
    return max(min_angle, min(max_angle, int(round(mapped))))


def apply_deadzone(angle: int, center_angle: int, deadzone_deg: int) -> int:
    if deadzone_deg < 0:
        raise ValueError("deadzone_deg must be non-negative")
    if abs(angle - center_angle) <= deadzone_deg:
        return center_angle
    return angle


def smooth_angle(last_angle: int | None, next_angle: int, max_step: int) -> int:
    if max_step < 0:
        raise ValueError("max_step must be non-negative")
    if last_angle is None or max_step == 0:
        return next_angle

    if next_angle > last_angle + max_step:
        return last_angle + max_step
    if next_angle < last_angle - max_step:
        return last_angle - max_step
    return next_angle


def should_send_angle(last_sent_angle: int | None, next_angle: int, threshold: int) -> bool:
    if threshold < 0:
        raise ValueError("threshold must be non-negative")
    if last_sent_angle is None:
        return True
    return abs(next_angle - last_sent_angle) >= threshold


def should_stop_for_loss(missed_frames: int, hold_frames: int) -> bool:
    if missed_frames < 0:
        raise ValueError("missed_frames must be non-negative")
    if hold_frames <= 0:
        raise ValueError("hold_frames must be positive")
    return missed_frames >= hold_frames
