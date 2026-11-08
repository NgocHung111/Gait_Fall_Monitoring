import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.main import (
    PHONE_LOCATION_MAX_AGE_SECONDS,
    PhoneLocationUpdate,
    Telemetry,
    TelemetryHistoryItem,
    _read_telemetry_history,
    _store_telemetry_record,
    get_telemetry_history,
    receive_telemetry,
    telemetry_history,
    update_phone_location,
)
from app.services.gait_analysis import GaitMetrics


class TelemetryHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        telemetry_history.clear()
        self.addCleanup(self._clear_phone_location)
        self.addCleanup(telemetry_history.clear)

    @staticmethod
    def _clear_phone_location() -> None:
        import app.main

        app.main.latest_phone_location = None

    def test_records_successful_telemetry_for_dashboard(self) -> None:
        response = receive_telemetry(
            Telemetry(steps=123, step_intervals_ms=[900, 1100])
        )

        history = get_telemetry_history(limit=1)
        self.assertEqual(response.steps, 123)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].steps, 123)
        self.assertEqual(history[0].gait.cadence_steps_per_minute, 60.0)
        self.assertIsNotNone(history[0].timestamp_utc)

    def test_step_update_is_stored_without_sending_telegram(self) -> None:
        response = receive_telemetry(
            Telemetry(
                event="STEP_UPDATE",
                steps=47,
                step_intervals_ms=[900, 1000],
            )
        )

        history = get_telemetry_history(limit=1)
        self.assertEqual(response.event, "STEP_UPDATE")
        self.assertEqual(response.steps, 47)
        self.assertFalse(response.telegram_sent)
        self.assertAlmostEqual(
            response.gait.cadence_steps_per_minute, 60.0 / 0.95
        )
        self.assertEqual(history[0].event, "STEP_UPDATE")

    def test_phone_location_is_used_for_fall_and_telegram(self) -> None:
        timestamp = datetime.now(timezone.utc)
        location = PhoneLocationUpdate(
            lat=10.7769,
            lng=106.7009,
            accuracy_m=8.5,
            timestamp_utc=timestamp,
        )

        with patch("app.main.PHONE_LOCATION_TOKEN", "test-token"):
            update_phone_location(location, authorization="Bearer test-token")

        with patch("app.main.send_emergency_message") as send_message:
            response = receive_telemetry(Telemetry(event="FALL_DETECTED", steps=12))

        self.assertEqual(response.location, {"lat": 10.7769, "lng": 106.7009})
        self.assertEqual(response.location_source, "phone")
        self.assertLessEqual(response.location_age_seconds, PHONE_LOCATION_MAX_AGE_SECONDS)
        send_message.assert_called_once()
        self.assertEqual(send_message.call_args.kwargs["latitude"], 10.7769)
        history = get_telemetry_history(limit=1)
        self.assertEqual(history[0].location_source, "phone")

    def test_phone_location_update_requires_configured_token(self) -> None:
        location = PhoneLocationUpdate(
            lat=10.0,
            lng=106.0,
            timestamp_utc=datetime.now(timezone.utc),
        )
        with patch("app.main.PHONE_LOCATION_TOKEN", "test-token"):
            with self.assertRaises(HTTPException) as error:
                update_phone_location(location, authorization="Bearer wrong-token")
        self.assertEqual(error.exception.status_code, 401)

    def test_expired_phone_location_is_not_attached_to_a_fall(self) -> None:
        stale_location = PhoneLocationUpdate(
            lat=10.0,
            lng=106.0,
            timestamp_utc=datetime.now(timezone.utc) - timedelta(minutes=3),
        )
        import app.main

        app.main.latest_phone_location = stale_location
        with patch("app.main.send_emergency_message"):
            response = receive_telemetry(Telemetry(event="FALL_DETECTED", steps=12))

        self.assertIsNone(response.location)
        self.assertIn("stale", response.location_error)

    def test_rejects_out_of_range_history_limit(self) -> None:
        with self.assertRaises(HTTPException):
            get_telemetry_history(limit=251)

    def test_reads_persisted_history_from_postgres(self) -> None:
        record = TelemetryHistoryItem(
            received=True,
            event="FALL_DETECTED",
            steps=12,
            gait=GaitMetrics(
                step_intervals_ms=[],
                cadence_steps_per_minute=None,
                mean_step_interval_ms=None,
                variability_cv_percent=None,
            ),
            location=None,
            location_error=None,
            telegram_sent=False,
            timestamp_utc=datetime.now(timezone.utc),
        )
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = [
            (record.model_dump(mode="json"),)
        ]
        connection_context = MagicMock()
        connection_context.__enter__.return_value = connection

        with (
            patch("app.main.DATABASE_URL", "postgresql://test"),
            patch("psycopg.connect", return_value=connection_context),
        ):
            history = _read_telemetry_history(limit=1)

        self.assertEqual(history, [record])
        connection.execute.assert_called_once()

    def test_persists_telemetry_to_postgres(self) -> None:
        record = TelemetryHistoryItem(
            received=True,
            event="FALL_DETECTED",
            steps=12,
            gait=GaitMetrics(
                step_intervals_ms=[],
                cadence_steps_per_minute=None,
                mean_step_interval_ms=None,
                variability_cv_percent=None,
            ),
            location=None,
            location_error=None,
            telegram_sent=False,
            timestamp_utc=datetime.now(timezone.utc),
        )
        connection = MagicMock()
        connection_context = MagicMock()
        connection_context.__enter__.return_value = connection

        with (
            patch("app.main.DATABASE_URL", "postgresql://test"),
            patch("psycopg.connect", return_value=connection_context),
        ):
            _store_telemetry_record(record)

        connection.execute.assert_called_once()
        self.assertEqual(connection.execute.call_args.args[1][0], record.timestamp_utc)


if __name__ == "__main__":
    unittest.main()
