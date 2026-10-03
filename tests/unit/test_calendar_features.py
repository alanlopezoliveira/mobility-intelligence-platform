from datetime import UTC, datetime

from src.ml.calendar_features import local_calendar_features


def test_madrid_fall_back_preserves_both_ambiguous_local_hours():
    first = local_calendar_features(datetime(2024, 10, 27, 0, 30, tzinfo=UTC), 'Europe/Madrid')
    second = local_calendar_features(datetime(2024, 10, 27, 1, 30, tzinfo=UTC), 'Europe/Madrid')

    assert first['local_hour'] == second['local_hour'] == 2
    assert first['dst_fold'] == 0
    assert second['dst_fold'] == 1
    assert first['utc_offset_minutes'] == 120
    assert second['utc_offset_minutes'] == 60
    assert first['weekend'] is True and second['weekend'] is True


def test_madrid_spring_forward_skips_the_missing_wall_clock_hour():
    before = local_calendar_features(datetime(2024, 3, 31, 0, 30, tzinfo=UTC), 'Europe/Madrid')
    after = local_calendar_features(datetime(2024, 3, 31, 1, 30, tzinfo=UTC), 'Europe/Madrid')

    assert before['local_hour'] == 1
    assert after['local_hour'] == 3
    assert before['utc_offset_minutes'] == 60
    assert after['utc_offset_minutes'] == 120


def test_madrid_normal_winter_day_has_expected_local_calendar():
    features = local_calendar_features(datetime(2024, 1, 10, 8, 0, tzinfo=UTC), 'Europe/Madrid')

    assert features['local_hour'] == 9
    assert features['local_weekday'] == 2
    assert features['weekend'] is False
    assert features['utc_offset_minutes'] == 60
    assert features['dst_fold'] == 0
