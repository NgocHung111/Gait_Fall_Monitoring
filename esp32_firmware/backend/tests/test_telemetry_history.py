import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.main import (
    Telemetry,
    TelemetryHistoryItem,
    _read_telemetry_history,
    _store_telemetry_record,
    get_telemetry_history,
    receive_telemetry,
    telemetry_history,
)
from app.services.gait_analysis import GaitMetrics


class TelemetryHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        telemetry_history.clear()

    def tearDown(self) -> None:
        telemetry_history.clear()

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
