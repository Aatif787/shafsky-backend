"""
Regression tests for Aviation Edge schedule-date handling (no network, no database).

Two provider quirks are covered:
  * `timetable` ignores the requested date and always answers with today's rows,
    including today's live actual/estimated times.
  * `flightsFuture` returns time-only values such as '09:30' and refuses any date
    nearer than roughly a week out.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.flight.exceptions import (
    FlightNotFoundException,
    FlightScheduleUnavailableException,
)
from app.flight.providers import aviation_edge_provider as aep
from app.flight.providers.aviation_edge_provider import (
    AviationEdgeProvider,
    _COVERAGE_ERROR_RE,
    _future_schedule_date_ok,
    _retarget_schedule_dates,
    _split_date_and_time,
)


@pytest.fixture(autouse=True)
def reset_learned_window():
    aep._LEARNED_FUTURE_MIN_DATE = None
    yield
    aep._LEARNED_FUTURE_MIN_DATE = None


def _days_out(days: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()


class TestSplitDateAndTime:
    def test_full_iso_timestamp(self):
        assert _split_date_and_time("2026-08-29T09:30:00.000") == ("2026-08-29", "09:30:00")

    def test_space_separated_timestamp(self):
        assert _split_date_and_time("2026-08-29 09:30:00") == ("2026-08-29", "09:30:00")

    def test_time_only_from_flights_future(self):
        assert _split_date_and_time("09:30") == (None, "09:30:00")

    def test_single_digit_hour_is_padded(self):
        assert _split_date_and_time("9:05") == (None, "09:05:00")

    def test_unparseable_values(self):
        assert _split_date_and_time(None) == (None, None)
        assert _split_date_and_time("") == (None, None)
        assert _split_date_and_time("not-a-time") == (None, None)


class TestRetargetScheduleDates:
    def test_stale_timetable_date_moves_to_requested_date(self):
        """timetable answers with today's rows; the travel date must win."""
        assert _retarget_schedule_dates(
            "2026-08-29T09:30:00.000", "2026-08-29T11:55:00.000", "2026-08-30"
        ) == ("2026-08-30T09:30:00", "2026-08-30T11:55:00")

    def test_time_only_values_gain_the_requested_date(self):
        assert _retarget_schedule_dates("09:30", "11:55", "2026-09-06") == (
            "2026-09-06T09:30:00",
            "2026-09-06T11:55:00",
        )

    def test_overnight_time_only_rolls_arrival_to_next_day(self):
        assert _retarget_schedule_dates("23:40", "01:20", "2026-09-06") == (
            "2026-09-06T23:40:00",
            "2026-09-07T01:20:00",
        )

    def test_existing_day_gap_is_preserved(self):
        assert _retarget_schedule_dates(
            "2026-08-29T23:40:00.000", "2026-08-30T01:20:00.000", "2026-09-10"
        ) == ("2026-09-10T23:40:00", "2026-09-11T01:20:00")

    def test_multi_day_gap_is_preserved(self):
        dep, arr = _retarget_schedule_dates(
            "2026-08-29T20:00:00.000", "2026-08-31T06:00:00.000", "2026-09-10"
        )
        assert dep == "2026-09-10T20:00:00"
        assert arr == "2026-09-12T06:00:00"

    def test_missing_values_pass_through(self):
        assert _retarget_schedule_dates(None, None, "2026-09-10") == (None, None)

    def test_no_target_date_is_a_noop(self):
        assert _retarget_schedule_dates("09:30", "11:55", None) == ("09:30", "11:55")

    def test_invalid_target_date_is_a_noop(self):
        assert _retarget_schedule_dates("09:30", "11:55", "garbage") == ("09:30", "11:55")


class TestFutureScheduleWindow:
    def test_near_dates_are_not_queried(self):
        assert _future_schedule_date_ok(_days_out(0)) is False
        assert _future_schedule_date_ok(_days_out(1)) is False
        assert _future_schedule_date_ok(_days_out(7)) is False

    def test_far_dates_are_queried(self):
        assert _future_schedule_date_ok(_days_out(8)) is True
        assert _future_schedule_date_ok(_days_out(45)) is True

    def test_malformed_date_is_rejected(self):
        assert _future_schedule_date_ok("30/08/2026") is False
        assert _future_schedule_date_ok("") is False

    def test_boundary_is_learned_from_provider_error(self):
        far = _days_out(30)
        assert _future_schedule_date_ok(far) is True
        aep._record_future_window_boundary(_days_out(40))
        # The provider said it serves nothing up to day 40, so day 30 is now skipped.
        assert _future_schedule_date_ok(far) is False
        assert _future_schedule_date_ok(_days_out(41)) is True

    def test_learned_boundary_never_moves_backwards(self):
        aep._record_future_window_boundary(_days_out(40))
        first = aep._LEARNED_FUTURE_MIN_DATE
        aep._record_future_window_boundary(_days_out(10))
        assert aep._LEARNED_FUTURE_MIN_DATE == first

    def test_garbage_boundary_is_ignored(self):
        aep._record_future_window_boundary("not-a-date")
        assert aep._LEARNED_FUTURE_MIN_DATE is None


class TestCoverageErrorDetection:
    @pytest.mark.parametrize(
        "message",
        [
            "date must be above 2026-09-05",
            "date must be above: 2026-09-05",
            "Date Must Be Above 2026-09-05",
            "date must be greater than 2026-09-05",
            "date must be after 2026-09-05",
        ],
    )
    def test_recognised_variants(self, message):
        match = _COVERAGE_ERROR_RE.search(message)
        assert match is not None
        assert match.group(1) == "2026-09-05"

    @pytest.mark.parametrize("message", ["No Record Found", "Invalid api key", ""])
    def test_unrelated_errors_are_not_treated_as_coverage_problems(self, message):
        assert _COVERAGE_ERROR_RE.search(message) is None


class TestNormalizationDropsForeignDayLiveData:
    """
    A future-dated booking must not inherit today's delay. Otherwise duration and
    the airport cutoff check are computed against a different operating day.
    """

    TIMETABLE_ROW = {
        "_source": "timetable",
        "airline": {"iataCode": "6E", "icaoCode": "IGO", "name": "IndiGo"},
        "flight": {"iataNumber": "6E6107", "number": "6107"},
        "departure": {
            "iataCode": "DEL",
            "scheduledTime": "2026-08-29T09:30:00.000",
            "estimatedTime": "2026-08-29T09:39:00.000",
            "actualTime": "2026-08-29T09:49:00.000",
            "delay": 19,
        },
        "arrival": {
            "iataCode": "BOM",
            "scheduledTime": "2026-08-29T11:55:00.000",
            "estimatedTime": "2026-08-29T11:30:00.000",
        },
    }

    def test_future_date_drops_live_fields_and_retargets_schedule(self):
        result = AviationEdgeProvider()._normalize_flight_data(
            self.TIMETABLE_ROW, date_context="2026-08-30"
        )
        assert result.departure.scheduled == "2026-08-30T09:30:00"
        assert result.arrival.scheduled == "2026-08-30T11:55:00"
        assert result.departure.estimated is None
        assert result.departure.actual is None
        assert result.departure.delay is None
        assert result.arrival.estimated is None
        # 09:30 -> 11:55 on the travel date
        assert result.duration.minutes == 145

    def test_same_day_keeps_live_fields(self):
        result = AviationEdgeProvider()._normalize_flight_data(
            self.TIMETABLE_ROW, date_context="2026-08-29"
        )
        assert result.departure.scheduled == "2026-08-29T09:30:00"
        assert result.departure.actual == "2026-08-29T09:49:00.000"
        assert result.departure.delay == 19

    def test_time_only_future_row_produces_a_real_duration(self):
        row = {
            "_source": "flightsFuture",
            "airline": {"iataCode": "6E", "name": "IndiGo"},
            "flight": {"iataNumber": "6e6107", "number": "6107"},
            "departure": {"iataCode": "del", "scheduledTime": "09:30"},
            "arrival": {"iataCode": "bom", "scheduledTime": "11:55"},
        }
        result = AviationEdgeProvider()._normalize_flight_data(row, date_context="2026-09-06")
        assert result.departure.scheduled == "2026-09-06T09:30:00"
        assert result.arrival.scheduled == "2026-09-06T11:55:00"
        assert result.duration.minutes == 145


class TestValidateFlightErrorSelection:
    def _provider_returning(self, side_effect):
        provider = AviationEdgeProvider()
        provider._get_cached_data = lambda *_a, **_k: None
        provider._set_cached_data = lambda *_a, **_k: None
        provider._make_request = side_effect
        return provider

    def test_coverage_error_reports_schedule_unavailable(self):
        def refuse_date(endpoint, params):
            raise aep._ScheduleOutOfProviderRange("date must be above 2026-09-05", "2026-09-05")

        provider = self._provider_returning(refuse_date)
        with pytest.raises(FlightScheduleUnavailableException) as excinfo:
            provider.validate_flight("6E6187", "2026-08-30", direction="departure", origin_code="DEL")
        assert excinfo.value.code == "FLIGHT_SCHEDULE_UNAVAILABLE"
        assert "manually" in excinfo.value.message

    def test_empty_provider_response_still_reports_not_found(self):
        provider = self._provider_returning(lambda endpoint, params: [])
        with pytest.raises(FlightNotFoundException) as excinfo:
            provider.validate_flight("6E6187", "2026-08-30", direction="departure", origin_code="DEL")
        assert excinfo.value.code == "FLIGHT_NOT_FOUND"
