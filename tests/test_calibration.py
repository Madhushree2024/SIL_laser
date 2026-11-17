from app.calibration.scale_calibration import pixel_points_to_mm, profile_from_reference_square


def test_scale_profile_from_reference_square() -> None:
    profile = profile_from_reference_square([[0, 0], [100, 0], [100, 100], [0, 100]], 100, (400, 300))
    assert round(profile.mm_per_pixel, 3) == 1.0
    converted = pixel_points_to_mm([[50, 50]], profile)
    assert round(converted[0][0], 1) == 50.0
    assert round(converted[0][1], 1) == 50.0
