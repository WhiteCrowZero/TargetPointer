from dataclasses import dataclass


BBox = tuple[int, int, int, int]


@dataclass(frozen=True)
class MatchResult:
    index: int
    score: float
    iou: float
    center_ratio: float
    area_change: float


def bbox_center(bbox: BBox) -> tuple[float, float]:
    x, y, width, height = bbox
    return x + width / 2.0, y + height / 2.0


def bbox_area(bbox: BBox) -> int:
    _, _, width, height = bbox
    return max(0, width) * max(0, height)


def bbox_diagonal(bbox: BBox) -> float:
    _, _, width, height = bbox
    return max(1.0, (width * width + height * height) ** 0.5)


def bbox_iou(first_bbox: BBox, second_bbox: BBox) -> float:
    first_x, first_y, first_width, first_height = first_bbox
    second_x, second_y, second_width, second_height = second_bbox

    first_right = first_x + first_width
    first_bottom = first_y + first_height
    second_right = second_x + second_width
    second_bottom = second_y + second_height

    inter_left = max(first_x, second_x)
    inter_top = max(first_y, second_y)
    inter_right = min(first_right, second_right)
    inter_bottom = min(first_bottom, second_bottom)

    inter_width = max(0, inter_right - inter_left)
    inter_height = max(0, inter_bottom - inter_top)
    intersection = inter_width * inter_height
    if intersection == 0:
        return 0.0

    union = bbox_area(first_bbox) + bbox_area(second_bbox) - intersection
    if union <= 0:
        return 0.0
    return intersection / union


def center_distance_ratio(previous_bbox: BBox, candidate_bbox: BBox) -> float:
    previous_center_x, previous_center_y = bbox_center(previous_bbox)
    candidate_center_x, candidate_center_y = bbox_center(candidate_bbox)
    delta_x = candidate_center_x - previous_center_x
    delta_y = candidate_center_y - previous_center_y
    distance = (delta_x * delta_x + delta_y * delta_y) ** 0.5
    return distance / bbox_diagonal(previous_bbox)


def area_change_ratio(previous_bbox: BBox, candidate_bbox: BBox) -> float:
    previous_area = bbox_area(previous_bbox)
    candidate_area = bbox_area(candidate_bbox)
    if previous_area <= 0:
        return 0.0
    return abs(candidate_area - previous_area) / previous_area


def match_target_bbox(
    previous_bbox: BBox,
    candidate_bboxes: list[BBox],
    min_iou: float,
    max_center_ratio: float,
    max_area_change: float,
) -> MatchResult | None:
    if min_iou < 0:
        raise ValueError("min_iou must be non-negative")
    if max_center_ratio <= 0:
        raise ValueError("max_center_ratio must be positive")
    if max_area_change <= 0:
        raise ValueError("max_area_change must be positive")

    best_match: MatchResult | None = None

    for index, candidate_bbox in enumerate(candidate_bboxes):
        iou = bbox_iou(previous_bbox, candidate_bbox)
        center_ratio = center_distance_ratio(previous_bbox, candidate_bbox)
        area_change = area_change_ratio(previous_bbox, candidate_bbox)

        if iou < min_iou:
            continue
        if center_ratio > max_center_ratio:
            continue
        if area_change > max_area_change:
            continue

        center_score = 1.0 - min(1.0, center_ratio / max_center_ratio)
        area_score = 1.0 - min(1.0, area_change / max_area_change)
        score = 0.55 * iou + 0.30 * center_score + 0.15 * area_score

        match = MatchResult(
            index=index,
            score=score,
            iou=iou,
            center_ratio=center_ratio,
            area_change=area_change,
        )
        if best_match is None or match.score > best_match.score:
            best_match = match

    return best_match


def smooth_center(
    last_center: tuple[float, float] | None,
    next_center: tuple[float, float],
    alpha: float,
) -> tuple[float, float]:
    if not 0 < alpha <= 1:
        raise ValueError("alpha must be between 0 and 1")
    if last_center is None:
        return next_center

    last_x, last_y = last_center
    next_x, next_y = next_center
    return (
        last_x * (1.0 - alpha) + next_x * alpha,
        last_y * (1.0 - alpha) + next_y * alpha,
    )


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


def hold_angle_if_within_threshold(current_angle: int | None, target_angle: int, hold_threshold: int) -> int:
    if hold_threshold < 0:
        raise ValueError("hold_threshold must be non-negative")
    if current_angle is None:
        return target_angle
    if abs(target_angle - current_angle) <= hold_threshold:
        return current_angle
    return target_angle


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


def resolve_angle_step(
    current_angle: int,
    target_angle: int,
    small_error_threshold: int,
    medium_error_threshold: int,
    small_step: int,
    medium_step: int,
    large_step: int,
) -> int:
    if small_error_threshold < 0:
        raise ValueError("small_error_threshold must be non-negative")
    if medium_error_threshold < small_error_threshold:
        raise ValueError("medium_error_threshold must be >= small_error_threshold")
    if small_step <= 0 or medium_step <= 0 or large_step <= 0:
        raise ValueError("angle steps must be positive")
    if not small_step <= medium_step <= large_step:
        raise ValueError("angle steps must be ordered small <= medium <= large")

    angle_error = abs(target_angle - current_angle)
    if angle_error <= small_error_threshold:
        return small_step
    if angle_error <= medium_error_threshold:
        return medium_step
    return large_step


def smooth_angle_adaptive(
    last_angle: int | None,
    target_angle: int,
    center_angle: int,
    small_error_threshold: int,
    medium_error_threshold: int,
    small_step: int,
    medium_step: int,
    large_step: int,
) -> int:
    current_angle = center_angle if last_angle is None else last_angle
    max_step = resolve_angle_step(
        current_angle,
        target_angle,
        small_error_threshold,
        medium_error_threshold,
        small_step,
        medium_step,
        large_step,
    )
    return smooth_angle(current_angle, target_angle, max_step)


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
