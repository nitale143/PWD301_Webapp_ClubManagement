"""Đọc ngày và giờ trong câu tiếng Việt cho bản nháp sự kiện (`extract_schedule`).

Hỗ trợ: 2026-12-12, 12/12, 12/12/2026, hôm nay, ngày mai; giờ 9:00, 9h, 9h30, 9 giờ, kèm buổi
(sáng/trưa/chiều/tối/đêm, vd "7 giờ tối" = 19:00). Không dùng am/pm. Ngày không có năm dùng năm
hiện tại theo giờ CLB, không tự đẩy sang năm sau. Giờ hoặc buổi mâu thuẫn → DomainError để hỏi lại
thay vì đoán.
"""

import re
from datetime import datetime, time, timedelta

from .common import DomainError, club_now


_ISO_DATE = re.compile(
    r"(?<!\w)(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})"
    r"(?=$|[\s,;.!?]|[Tt]\d{1,2}:\d{2}(?::\d{2})?(?![\w:]))"
)
_ISO_T_TIMESTAMP = re.compile(
    r"(?<!\w)(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})"
    r"[Tt](?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?(?![\w:])"
)
_VN_DATE = re.compile(
    r"(?<![\w/])(?:(?:ngày|ngay)\s*)?"
    r"(?P<day>\d{1,2})/(?P<month>\d{1,2})(?:/(?P<year>\d{4}))?(?![\d/])",
    re.I,
)
_RELATIVE_DATE = re.compile(
    r"(?<!\w)(?:(?:ngày|ngay)\s*(?:hôm\s+nay|hom\s+nay|mai)|"
    r"hôm\s+nay|hom\s+nay)(?=$|[\s,;.!?]|\d)",
    re.I,
)
_TIME = re.compile(
    r"(?<!\d)(?P<hour>\d{1,2})"
    r"(?:(?P<separator>\s*:\s*|\s*h\s*)(?P<minute>\d{1,2})?"
    r"(?::(?P<second>\d{1,2}))?|"
    r"(?P<unit>\s*(?:giờ|gio))(?:\s+(?P<word_minute>\d{1,2}))?)"
    r"(?![\w/:])",
    re.I,
)
_INVALID_CLOCK = re.compile(
    r"(?<![\w:])(?P<hour>\d{1,2})(?P<separator>\s*h\s*|\s*:\s*)"
    r"(?P<minute>\d{0,2})(?::(?P<second>\d{1,2}))?(?![\w/:])",
    re.I,
)
_PERIOD = r"(?:sáng|sang|trưa|trua|chiều|chieu|tối|toi|đêm|dem)"
_PERIOD_BEFORE = re.compile(
    rf"(?:(?P<first>{_PERIOD})\s+)?(?P<period>{_PERIOD})\s*$", re.I
)
_PERIOD_AFTER = re.compile(
    rf"\s*(?P<period>{_PERIOD})\b(?:\s+(?P<second>{_PERIOD})\b)?", re.I
)
_PAIR_GAP = re.compile(
    r"[\s,]*(?:[Tt]\s*)?(?:(?:lúc|luc|vào|vao|ngày|ngay|hạn\s+là|han\s+la|"
    r"từ|tu|from|to|at)\s*[\s,]*)*",
    re.I,
)
def _date_value(year, month, day, now):
    # Keeping an omitted year in this calendar year avoids guessing user intent.
    """Tạo ngày từ (năm, tháng, ngày); thiếu năm thì dùng năm hiện tại; ngày không tồn tại → lỗi."""
    resolved_year = int(year) if year else now.year
    try:
        return datetime(resolved_year, int(month), int(day)).date()
    except ValueError:
        raise DomainError("Ngày hoặc giờ trong yêu cầu không hợp lệ.") from None


def _extract_dates(message, now):
    """Tìm các ngày trong câu (ISO, dd/mm[/yyyy], hôm nay, ngày mai…) kèm vị trí."""
    dates = []
    occupied = []
    for match in _ISO_DATE.finditer(message):
        date_value = _date_value(match["year"], match["month"], match["day"], now)
        dates.append({"start": match.start(), "end": match.end(), "date": date_value,
                      "relative": False})
        occupied.append(match.span())
    for match in _VN_DATE.finditer(message):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        date_value = _date_value(match["year"], match["month"], match["day"], now)
        dates.append({"start": match.start(), "end": match.end(), "date": date_value,
                      "relative": False})
        occupied.append(match.span())
    for match in _RELATIVE_DATE.finditer(message):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        normalized = match.group().casefold()
        day_offset = 1 if "mai" in normalized else 0
        date_value = (now + timedelta(days=day_offset)).date()
        dates.append({"start": match.start(), "end": match.end(), "date": date_value,
                      "relative": True})
        occupied.append(match.span())
    dates.sort(key=lambda item: item["start"])
    coalesced = []
    for item in dates:
        if coalesced:
            previous = coalesced[-1]
            gap = message[previous["end"]:item["start"]]
            if (previous["date"] == item["date"] and len(gap) <= 12 and
                    _PAIR_GAP.fullmatch(gap)):
                previous["end"] = item["end"]
                previous["relative"] = previous["relative"] or item["relative"]
                continue
        coalesced.append(item)
    return coalesced


def _period_key(value):
    """Đổi buổi trong ngày (sáng/trưa/chiều/tối/đêm) thành khóa chuẩn."""
    folded = value.casefold()
    return {
        "sáng": "morning", "sang": "morning",
        "trưa": "noon", "trua": "noon",
        "chiều": "afternoon", "chieu": "afternoon",
        "tối": "evening", "toi": "evening",
        "đêm": "night", "dem": "night",
    }[folded]


def _resolve_hour(hour, minute, period):
    """Đổi giờ theo buổi (vd: 7 giờ tối → 19:00), kiểm tra giờ hợp lệ."""
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise DomainError("Giờ trong yêu cầu không hợp lệ.")
    if period is None:
        return hour, minute

    if period == "morning":
        if hour == 12:
            hour = 0
        elif hour > 12:
            raise DomainError("Giờ và buổi sáng mâu thuẫn; hãy xác nhận lại.")
    elif period == "noon":
        # Common Vietnamese noon forms are 11 and 12; other 12-hour values
        # need clarification instead of an invented AM/PM conversion.
        if hour not in {11, 12, 13}:
            raise DomainError("Giờ buổi trưa chưa rõ; hãy ghi giờ 24h hoặc xác nhận lại.")
    elif period == "afternoon":
        if 1 <= hour <= 6:
            hour += 12
        elif hour not in {12, 13, 14, 15, 16, 17, 18}:
            raise DomainError("Giờ và buổi chiều mâu thuẫn; hãy xác nhận lại.")
    elif period == "evening":
        if 6 <= hour <= 11:
            hour += 12
        elif hour not in {18, 19, 20, 21, 22, 23}:
            raise DomainError("Giờ và buổi tối mâu thuẫn; hãy xác nhận lại.")
    elif period == "night":
        if 6 <= hour <= 11:
            hour += 12
        elif hour == 12:
            hour = 0
        elif hour not in {0, 1, 2, 3, 4, 5, 18, 19, 20, 21, 22, 23}:
            raise DomainError("Giờ và buổi đêm mâu thuẫn; hãy xác nhận lại.")
    return hour, minute


def _extract_times(message, dates):
    """Tìm các mốc giờ trong câu kèm vị trí và buổi."""
    times = []
    iso_clock_starts = {match.start("hour") for match in _ISO_T_TIMESTAMP.finditer(message)}

    for match in _TIME.finditer(message):
        if any(item["clock_start"] == match.start() for item in times):
            continue
        previous = message[match.start() - 1] if match.start() else ""
        if previous and (previous.isalnum() or previous == "_") and not any(
                item["relative"] and item["end"] == match.start() for item in dates) and (
                    match.start() not in iso_clock_starts):
            continue
        separator = match["separator"]
        if separator and ":" in separator and match["minute"] is None:
            raise DomainError("Giờ trong yêu cầu không hợp lệ.")
        if match["second"] and (":" not in (separator or "") or not match["minute"]):
            raise DomainError("Giờ trong yêu cầu không hợp lệ.")
        minute_text = match["minute"] or match["word_minute"] or "0"
        hour, minute = int(match["hour"]), int(minute_text)
        second = int(match["second"] or 0)
        if not 0 <= second <= 59:
            raise DomainError("Giờ trong yêu cầu không hợp lệ.")

        before = _PERIOD_BEFORE.search(message, 0, match.start())
        after = _PERIOD_AFTER.match(message, match.end())
        before_periods = ([_period_key(before["first"])] if before and before["first"] else [])
        if before:
            before_periods.append(_period_key(before["period"]))
        after_periods = ([_period_key(after["period"])] if after else [])
        if after and after["second"]:
            after_periods.append(_period_key(after["second"]))
        period_keys = before_periods + after_periods
        if len(set(period_keys)) > 1:
            raise DomainError("Yêu cầu nêu hai buổi khác nhau cho cùng một giờ; hãy xác nhận lại.")
        period = period_keys[0] if period_keys else None
        if not separator and not match["unit"] and period is None:
            # Bare numbers in names, dates, or task descriptions are not times.
            continue
        hour, minute = _resolve_hour(hour, minute, period)
        start = before.start("first") if before and before["first"] else (
            before.start("period") if before else match.start())
        end = match.end()
        if after:
            end = after.end()
        try:
            parsed_time = time(hour, minute, second)
        except ValueError:
            raise DomainError("Giờ trong yêu cầu không hợp lệ.") from None
        times.append({"start": start, "end": end, "clock_start": match.start(),
                      "time": parsed_time})

    valid_starts = {item["clock_start"] for item in times}
    for match in _INVALID_CLOCK.finditer(message):
        if (match.start() not in valid_starts and not any(
                item["clock_start"] <= match.start() < item["end"] for item in times)):
            raise DomainError("Giờ trong yêu cầu không hợp lệ.")
    times.sort(key=lambda item: item["start"])
    return times


def extract_schedule(message):
    """Ngày và giờ xuất hiện trong câu, theo thứ tự.

    Trả về {"dates": [date…], "times": [time…], "spans": [(start, end)…]}; `spans` là vị trí các
    cụm ngày/giờ để phần còn lại của câu (tên, địa điểm…) không lẫn ngày giờ.
    """
    now = club_now()
    dates = _extract_dates(message, now)
    times = _extract_times(message, dates)
    return {"dates": [item["date"] for item in dates],
            "times": [item["time"] for item in times],
            "spans": sorted([(item["start"], item["end"]) for item in dates + times])}
