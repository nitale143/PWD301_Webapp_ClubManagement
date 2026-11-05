"""Đọc ngày giờ tiếng Việt trong câu chat (extract_schedule), với đồng hồ CLB cố định."""

import unittest
from datetime import date, datetime, time
from unittest.mock import patch

from app.services.common import DomainError
from app.services.natural_datetime import extract_schedule


FIXED_NOW = datetime(2026, 10, 5, 12, 0)


class NaturalDateTimeTests(unittest.TestCase):
    def _parse(self, message):
        """[(datetime, cụm chữ)] ghép ngày với giờ theo thứ tự, như bản nháp sự kiện dùng."""
        with patch("app.services.natural_datetime.club_now", return_value=FIXED_NOW):
            found = extract_schedule(message)
        self.assertTrue(all(start < end for start, end in found["spans"]))
        day = found["dates"][0] if found["dates"] else None
        return [(datetime.combine(day, clock), message) for clock in found["times"]] if day else []

    def _assert_one_date(self, message, expected):
        result = self._parse(message)
        self.assertEqual(len(result), 1, result)
        self.assertEqual(result[0][0], expected)

    def test_date_and_time_order_and_vietnamese_time_forms(self):
        cases = (
            ("23:00 10/10", datetime(2026, 10, 10, 23, 0)),
            ("10/10 lúc 23:00", datetime(2026, 10, 10, 23, 0)),
            ("10/10 lúc 23h", datetime(2026, 10, 10, 23, 0)),
            ("10/10 lúc 23h30", datetime(2026, 10, 10, 23, 30)),
            ("23h ngày 10/10", datetime(2026, 10, 10, 23, 0)),
            ("11 giờ tối ngày 10/10", datetime(2026, 10, 10, 23, 0)),
            ("tối 23:00 10/10", datetime(2026, 10, 10, 23, 0)),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                self._assert_one_date(message, expected)

    def test_today_and_tomorrow_are_relative_to_fixed_club_date(self):
        self._assert_one_date("hôm nay lúc 23:00", datetime(2026, 10, 5, 23, 0))
        self._assert_one_date("ngày mai 11 giờ tối", datetime(2026, 10, 6, 23, 0))

    def test_full_iso_and_vietnamese_date_formats_remain_supported(self):
        cases = (
            ("2026-10-10 23:00", datetime(2026, 10, 10, 23, 0)),
            ("2026-10-10T23:00", datetime(2026, 10, 10, 23, 0)),
            ("10/10/2026 lúc 23:00", datetime(2026, 10, 10, 23, 0)),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                self._assert_one_date(message, expected)

    def test_event_time_range_uses_one_explicit_date_for_both_times(self):
        result = self._parse("Ngày 10/10/2026 từ 19:00 đến 21:00")
        self.assertEqual(len(result), 2, result)
        self.assertEqual([when for when, _raw in result], [
            datetime(2026, 10, 10, 19, 0),
            datetime(2026, 10, 10, 21, 0),
        ])
        self.assertTrue(all(raw.strip() for _when, raw in result))

    def test_missing_year_uses_current_club_year_without_rolling_past_dates(self):
        self._assert_one_date("23:00 10/10", datetime(2026, 10, 10, 23, 0))
        # April 10 is already past on the fixed October 5 clock; the parser
        # must still use 2026 instead of silently inventing 2027.
        self._assert_one_date("10/04 lúc 09:00", datetime(2026, 4, 10, 9, 0))

    def test_invalid_calendar_and_clock_values_are_rejected(self):
        for message in (
            "32/10/2026 lúc 23:00",
            "10/13/2026 lúc 23:00",
            "29/02/2026 lúc 12:00",
            "10/10/2026 lúc 24:00",
            "10/10/2026 lúc 23:60",
            "10/10 23h:75",
            "10/10 12:00:99",
        ):
            with self.subTest(message=message):
                with self.assertRaises(DomainError):
                    self._parse(message)

    def test_every_date_is_reported_in_order_so_callers_can_detect_conflicts(self):
        with patch("app.services.natural_datetime.club_now", return_value=FIXED_NOW):
            found = extract_schedule("ngày mai 10/10/2026 lúc 23:00")
        self.assertEqual(found["dates"], [date(2026, 10, 6), date(2026, 10, 10)])
        self.assertEqual(found["times"], [time(23, 0)])

    def test_period_words_resolve_evening_hours_without_guessing_a_different_hour(self):
        self._assert_one_date("11 giờ tối 10/10/2026", datetime(2026, 10, 10, 23, 0))
        self._assert_one_date("10/10 11 giờ tối", datetime(2026, 10, 10, 23, 0))
        self._assert_one_date("tối 23:00 10/10/2026", datetime(2026, 10, 10, 23, 0))
        self._assert_one_date("10/10 đêm 1h", datetime(2026, 10, 10, 1, 0))
        for message in ("7 giờ sáng tối 10/10/2026", "10/10 chiều 0h", "10/10 tối 1h"):
            with self.subTest(message=message), self.assertRaises(DomainError):
                self._parse(message)



if __name__ == "__main__":
    unittest.main()
