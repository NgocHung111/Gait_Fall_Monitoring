import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.core.auth import (
    create_access_token,
    decode_access_token,
    hash_device_code,
    hash_password,
)
from app.main import (
    AccessTokenResponse,
    AlertContact,
    AlertContactCreate,
    AlertContactStatus,
    AuthenticatedUser,
    PHONE_LOCATION_MAX_AGE_SECONDS,
    PhoneLocationUpdate,
    RegisterRequest,
    StepResetState,
    Telemetry,
    TelemetryHistoryItem,
    _get_authenticated_user,
    _read_phone_location,
    _read_step_reset_state,
    _read_telemetry_history,
    _request_step_reset,
    _store_phone_location,
    _store_telemetry_record,
    add_alert_contact,
    get_alert_contacts,
    get_telemetry_history,
    login_user,
    receive_telemetry,
    register_user,
    reset_device_steps,
    telemetry_history,
    update_alert_contact,
    update_phone_location,
)
from app.services.gait_analysis import GaitMetrics


class AuthenticationAndTelemetryTests(unittest.TestCase):
    def setUp(self) -> None:
        import app.main

        self.device_code = "A" * 64
        self.user = AuthenticatedUser(
            id=1,
            username="alice",
            device_id=hash_device_code(self.device_code),
        )
        self.other_user = AuthenticatedUser(
            id=2,
            username="bob",
            device_id=hash_device_code("B" * 64),
        )
        telemetry_history.clear()
        app.main.latest_phone_location.clear()
        app.main.step_reset_revisions.clear()
        app.main.alert_contacts.clear()
        app.main.alert_contacts_by_device.clear()
        app.main.next_alert_contact_id = 1
        self.database_patch = patch("app.main.DATABASE_URL", "")
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        self.addCleanup(telemetry_history.clear)
        self.addCleanup(app.main.latest_phone_location.clear)
        self.addCleanup(app.main.step_reset_revisions.clear)
        self.addCleanup(app.main.alert_contacts_by_device.clear)

    def _receive(self, telemetry: Telemetry):
        telemetry.device_id = self.device_code
        with patch("app.main._get_device_owner", return_value=self.user):
            return receive_telemetry(telemetry)

    def test_records_successful_telemetry_for_dashboard(self) -> None:
        response = self._receive(
            Telemetry(steps=123, step_intervals_ms=[900, 1100])
        )

        history = get_telemetry_history(limit=1, current_user=self.user)
        self.assertEqual(response.steps, 123)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].steps, 123)
        self.assertEqual(history[0].gait.cadence_steps_per_minute, 60.0)
        self.assertIsNotNone(history[0].timestamp_utc)

    def test_history_is_scoped_to_the_authenticated_device(self) -> None:
        self._receive(Telemetry(event="STEP_UPDATE", steps=2))
        other_record = TelemetryHistoryItem(
            received=True,
            event="STEP_UPDATE",
            steps=99,
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
            device_id=self.other_user.device_id,
        )
        _store_telemetry_record(other_record, self.other_user.device_id)

        own_history = get_telemetry_history(current_user=self.user)
        other_history = get_telemetry_history(current_user=self.other_user)
        self.assertEqual([record.steps for record in own_history], [2])
        self.assertEqual([record.steps for record in other_history], [99])

    def test_step_update_is_stored_without_sending_telegram(self) -> None:
        response = self._receive(
            Telemetry(
                event="STEP_UPDATE",
                steps=47,
                step_intervals_ms=[900, 1000],
            )
        )

        history = get_telemetry_history(limit=1, current_user=self.user)
        self.assertEqual(response.event, "STEP_UPDATE")
        self.assertEqual(response.steps, 47)
        self.assertFalse(response.telegram_sent)
        self.assertAlmostEqual(response.gait.cadence_steps_per_minute, 60.0 / 0.95)
        self.assertEqual(history[0].event, "STEP_UPDATE")

    def test_contact_list_and_changes_are_scoped_to_device(self) -> None:
        contact = add_alert_contact(
            AlertContactCreate(name="  Alex  ", chat_id="123456"),
            current_user=self.user,
        )
        listed = get_alert_contacts(current_user=self.user)
        self.assertEqual(get_alert_contacts(current_user=self.other_user), [])
        disabled = update_alert_contact(
            contact.id,
            AlertContactStatus(active=False),
            current_user=self.user,
        )
        self.assertTrue(listed[0].active)
        self.assertFalse(disabled.active)

    def test_duplicate_contact_chat_id_is_rejected_for_same_device(self) -> None:
        add_alert_contact(
            AlertContactCreate(name="Alex", chat_id="123456"),
            current_user=self.user,
        )
        add_alert_contact(
            AlertContactCreate(name="Sam", chat_id="123456"),
            current_user=self.other_user,
        )
        with self.assertRaises(HTTPException) as error:
            add_alert_contact(
                AlertContactCreate(name="Sam", chat_id="123456"),
                current_user=self.user,
            )
        self.assertEqual(error.exception.status_code, 409)

    def test_fall_alert_sends_to_device_contacts_and_legacy_recipient(self) -> None:
        import app.main

        app.main.alert_contacts_by_device[self.user.device_id] = [
            AlertContact(id=1, name="Alex", chat_id="789012", active=True),
            AlertContact(id=2, name="Sam", chat_id="654321", active=False),
        ]
        settings = SimpleNamespace(
            telegram_bot_token=SimpleNamespace(get_secret_value=lambda: "bot-token"),
            telegram_chat_id="123456",
        )
        with (
            patch("app.main.settings", settings),
            patch("app.main.send_emergency_message") as send_message,
        ):
            response = self._receive(Telemetry(event="FALL_DETECTED", steps=12))

        self.assertTrue(response.telegram_sent)
        self.assertEqual(
            [call.kwargs["chat_id"] for call in send_message.call_args_list],
            ["123456", "789012"],
        )

    def test_phone_location_is_isolated_and_attached_to_the_correct_fall(self) -> None:
        location = PhoneLocationUpdate(
            lat=10.7769,
            lng=106.7009,
            accuracy_m=8.5,
            timestamp_utc=datetime.now(timezone.utc),
        )
        update_phone_location(location, current_user=self.user)
        self.assertIsNone(_read_phone_location(self.other_user.device_id))

        with patch("app.main.send_emergency_message"):
            response = self._receive(Telemetry(event="FALL_DETECTED", steps=12))

        self.assertEqual(response.location, {"lat": 10.7769, "lng": 106.7009})
        self.assertEqual(response.location_source, "phone")
        self.assertLessEqual(
            response.location_age_seconds, PHONE_LOCATION_MAX_AGE_SECONDS
        )

    def test_step_reset_revisions_are_isolated_per_device(self) -> None:
        state = reset_device_steps(current_user=self.user)
        other_state = _read_step_reset_state(self.other_user.device_id)

        self.assertEqual(state, StepResetState(reset_revision=1))
        self.assertEqual(other_state, StepResetState(reset_revision=0))
        self.assertEqual(_request_step_reset(self.other_user.device_id).reset_revision, 1)

    def test_rejects_stale_phone_location(self) -> None:
        location = PhoneLocationUpdate(
            lat=10.0,
            lng=106.0,
            timestamp_utc=datetime.now(timezone.utc) - timedelta(minutes=3),
        )
        with self.assertRaises(HTTPException) as error:
            update_phone_location(location, current_user=self.user)
        self.assertEqual(error.exception.status_code, 422)

    def test_registration_hashes_the_device_code_and_password(self) -> None:
        user = self.user
        with (
            patch("app.main.DATABASE_URL", "postgresql://test"),
            patch("app.main.JWT_SECRET_KEY", "s" * 32),
            patch("app.main._create_user", return_value=user) as create_user,
        ):
            result = register_user(
                RegisterRequest(
                    username="Alice",
                    password="correct horse battery",
                    device_id=self.device_code,
                )
            )

        self.assertEqual(result.username, "alice")
        self.assertNotEqual(
            create_user.call_args.args[1], "correct horse battery"
        )
        self.assertEqual(
            create_user.call_args.args[2], hash_device_code(self.device_code)
        )

    def test_login_returns_a_signed_access_token(self) -> None:
        password_hash = hash_password("correct horse battery")
        stored_user = self.user.model_copy(
            update={"hashed_password": password_hash}
        )
        secret = "s" * 32
        with (
            patch("app.main.DATABASE_URL", "postgresql://test"),
            patch("app.main.JWT_SECRET_KEY", secret),
            patch("app.main._read_user_by_username", return_value=stored_user),
        ):
            result = login_user(
                SimpleNamespace(username="alice", password="correct horse battery")
            )

        self.assertIsInstance(result, AccessTokenResponse)
        self.assertEqual(decode_access_token(result.access_token, secret), "1")

    def test_invalid_or_expired_access_token_is_rejected(self) -> None:
        with (
            patch("app.main.DATABASE_URL", "postgresql://test"),
            patch("app.main.JWT_SECRET_KEY", "s" * 32),
        ):
            with self.assertRaises(HTTPException) as error:
                _get_authenticated_user(None)
        self.assertEqual(error.exception.status_code, 401)

    def test_reads_persisted_history_with_device_filter(self) -> None:
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
            history = _read_telemetry_history(
                limit=1, device_id=self.user.device_id
            )

        self.assertEqual(history, [record.model_copy(update={"device_id": self.user.device_id})])
        self.assertIn("device_id = %s", connection.execute.call_args.args[0])
        self.assertEqual(connection.execute.call_args.args[1][0], self.user.device_id)

    def test_persists_telemetry_device_id_separately(self) -> None:
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
            _store_telemetry_record(record, self.user.device_id)

        self.assertIn("device_id", connection.execute.call_args.args[0])
        self.assertEqual(
            connection.execute.call_args.args[1][1], self.user.device_id
        )

    def test_rejects_out_of_range_history_limit(self) -> None:
        with self.assertRaises(HTTPException):
            get_telemetry_history(limit=251, current_user=self.user)


if __name__ == "__main__":
    unittest.main()
