"""
Google Calendar Tool for AI Voice Receptionist
==============================================
Handles Google Calendar API authentication using a Service Account (`credentials.json`),
ISO-8601 timestamp parsing with timezone awareness, event booking, and slot availability checks.
"""

import os
import sys
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from dateutil import parser as date_parser
from dotenv import load_dotenv

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from google.oauth2 import service_account
from googleapiclient.discovery import build, Resource
from googleapiclient.errors import HttpError

# Load environment variables
load_dotenv()

logger = logging.getLogger("calendar-tool")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Scopes required for managing calendar appointments
CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GoogleCalendarManager:
    """Manages Google Calendar operations using a Service Account."""

    def __init__(
        self,
        credentials_path: Optional[str] = None,
        calendar_id: Optional[str] = None,
        default_timezone: Optional[str] = None,
    ):
        """
        Initialize the Google Calendar manager.

        :param credentials_path: Path to Google Service Account JSON key file.
        :param calendar_id: Target Calendar ID (e.g. 'primary' or user's email).
        :param default_timezone: Timezone name (e.g. 'America/New_York').
        """
        self.credentials_path = (
            credentials_path
            or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
            or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            or "credentials.json"
        )
        self.calendar_id = calendar_id or os.getenv("GOOGLE_CALENDAR_ID") or "primary"
        self.timezone_name = default_timezone or os.getenv("TIMEZONE") or "America/New_York"

        self._service: Optional[Resource] = None
        self._tz = self._load_timezone(self.timezone_name)

    def _load_timezone(self, tz_name: str) -> ZoneInfo:
        """Safely load timezone with UTC fallback."""
        try:
            return ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            logger.warning(f"Timezone '{tz_name}' not found. Falling back to UTC.")
            return ZoneInfo("UTC")

    @property
    def timezone(self) -> ZoneInfo:
        return self._tz

    def is_configured(self) -> bool:
        """Check if service account credentials file exists on disk."""
        return os.path.exists(self.credentials_path)

    def get_service(self) -> Resource:
        """
        Authenticate and return the Google Calendar service resource.
        Caches the client after first initialization.
        """
        if self._service is not None:
            return self._service

        if not os.path.exists(self.credentials_path):
            raise FileNotFoundError(
                f"Google Calendar credentials file not found at '{self.credentials_path}'. "
                f"Please download your Service Account JSON key from Google Cloud Console "
                f"and save it as '{self.credentials_path}' or set GOOGLE_SERVICE_ACCOUNT_FILE in .env."
            )

        try:
            credentials = service_account.Credentials.from_service_account_file(
                self.credentials_path,
                scopes=CALENDAR_SCOPES,
            )
            self._service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
            logger.info(
                f"Google Calendar API service initialized successfully (calendar_id='{self.calendar_id}', "
                f"service_account='{credentials.service_account_email}')."
            )
            return self._service
        except Exception as e:
            logger.error(f"Failed to authenticate with Google Calendar API: {e}", exc_info=True)
            raise

    def parse_datetime(self, dt_str: str, override_tz: Optional[str] = None) -> datetime:
        """
        Parse an arbitrary datetime string (ISO 8601 or natural format)
        and ensure it is timezone-aware.
        """
        tz = self._load_timezone(override_tz) if override_tz else self._tz

        try:
            # First attempt standard ISO or dateutil parse
            parsed = date_parser.parse(dt_str)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Could not parse datetime string '{dt_str}'. Error: {e}")

        # If naive (no timezone attached), attach local configured timezone
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        else:
            # Convert to target timezone
            parsed = parsed.astimezone(tz)

        # Time intelligence: If the requested time is for today but has already passed by >15 minutes,
        # roll it forward to tomorrow automatically.
        now = datetime.now(tz)
        if parsed.date() == now.date() and (now - parsed).total_seconds() > 15 * 60:
            logger.info(
                f"Requested time {parsed.strftime('%I:%M %p')} has already passed today ({now.strftime('%I:%M %p')}). "
                f"Rolling forward to tomorrow at {parsed.strftime('%I:%M %p')}."
            )
            parsed = parsed + timedelta(days=1)

        return parsed

    def check_availability(
        self,
        start_time: str,
        duration_minutes: int = 30,
        end_time: Optional[str] = None,
        timezone: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Check if the requested time slot has any conflicting events.

        :param start_time: Start date and time in ISO format (e.g., '2026-10-27T14:00:00').
        :param duration_minutes: Duration of appointment in minutes.
        :param end_time: Optional explicit end time in ISO format.
        :param timezone: Optional timezone override.
        :return: Dict containing availability status and any conflict details.
        """
        if not self.is_configured():
            logger.warning("Google Calendar credentials not configured. Returning mock availability.")
            return {
                "available": True,
                "note": "Dry-run / unconfigured mode: slot assumed available.",
                "start": start_time,
            }

        try:
            service = self.get_service()
            start_dt = self.parse_datetime(start_time, timezone)

            if end_time:
                end_dt = self.parse_datetime(end_time, timezone)
            else:
                end_dt = start_dt + timedelta(minutes=duration_minutes)

            # Query overlapping events
            events_result = (
                service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=start_dt.isoformat(),
                    timeMax=end_dt.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )

            items: List[Dict[str, Any]] = events_result.get("items", [])
            active_conflicts = [
                {
                    "summary": item.get("summary", "Busy"),
                    "start": item.get("start", {}).get("dateTime", item.get("start", {}).get("date")),
                    "end": item.get("end", {}).get("dateTime", item.get("end", {}).get("date")),
                }
                for item in items
                if item.get("status") != "cancelled"
            ]

            if active_conflicts:
                formatted_start = start_dt.strftime("%A, %B %d at %I:%M %p")
                return {
                    "available": False,
                    "conflicts": active_conflicts,
                    "message": (
                        f"There is a scheduling conflict at {formatted_start} with '{active_conflicts[0]['summary']}'. "
                        "Please suggest an alternate time or day to the caller."
                    ),
                }

            formatted_slot = f"{start_dt.strftime('%A, %B %d at %I:%M %p')} - {end_dt.strftime('%I:%M %p')}"
            return {
                "available": True,
                "slot": formatted_slot,
                "start_iso": start_dt.isoformat(),
                "end_iso": end_dt.isoformat(),
                "message": f"The slot on {formatted_slot} is open and available for booking.",
            }

        except HttpError as err:
            logger.error(f"Google Calendar API HTTP error in check_availability: {err}")
            return {
                "available": False,
                "error": f"Calendar API error: {err.reason}",
                "message": "Unable to verify calendar availability right now due to a system error.",
            }
        except Exception as e:
            logger.error(f"Error checking availability: {e}", exc_info=True)
            return {
                "available": False,
                "error": str(e),
                "message": f"Could not check availability: {str(e)}",
            }

    def book_appointment(
        self,
        summary: str,
        start_time: str,
        duration_minutes: int = 30,
        end_time: Optional[str] = None,
        description: Optional[str] = "",
        attendee_email: Optional[str] = "",
        location: Optional[str] = "",
        timezone: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Book an appointment directly into Google Calendar.

        :param summary: Title/purpose of the appointment (e.g. 'Consultation with John Doe').
        :param start_time: ISO-8601 start time (e.g. '2026-10-27T14:00:00').
        :param duration_minutes: Duration in minutes (default 30).
        :param end_time: Optional explicit ISO end time.
        :param description: Optional notes, phone number, or agenda.
        :param attendee_email: Optional caller email for calendar invite.
        :param location: Optional physical address or video link.
        :param timezone: Optional timezone override.
        :return: Structured result dict with confirmation details.
        """
        if not self.is_configured():
            msg = (
                f"Dry-run: Credentials file '{self.credentials_path}' not found. "
                f"Simulated booking for '{summary}' at {start_time}."
            )
            logger.warning(msg)
            return {
                "status": "confirmed_mock",
                "summary": summary,
                "start": start_time,
                "message": (
                    f"I have noted your appointment for {summary} at {start_time}. "
                    "(Note: Connect Google Calendar credentials.json to persist to real calendar)."
                ),
            }

        try:
            service = self.get_service()
            start_dt = self.parse_datetime(start_time, timezone)

            if end_time:
                end_dt = self.parse_datetime(end_time, timezone)
            else:
                end_dt = start_dt + timedelta(minutes=duration_minutes)

            if end_dt <= start_dt:
                return {
                    "status": "error",
                    "message": "Appointment end time must be strictly after the start time.",
                }

            tz_str = timezone or self.timezone_name

            event_body: Dict[str, Any] = {
                "summary": summary,
                "description": description or "Booked via AI Voice Receptionist.",
                "start": {
                    "dateTime": start_dt.isoformat(),
                    "timeZone": tz_str,
                },
                "end": {
                    "dateTime": end_dt.isoformat(),
                    "timeZone": tz_str,
                },
                "reminders": {
                    "useDefault": False,
                    "overrides": [
                        {"method": "email", "minutes": 24 * 60},  # 24 hours before
                        {"method": "popup", "minutes": 15},       # 15 minutes before
                    ],
                },
            }

            if attendee_email and "@" in attendee_email:
                event_body["attendees"] = [{"email": attendee_email.strip()}]

            if location:
                event_body["location"] = location

            send_updates = "all" if (attendee_email and "@" in attendee_email) else "none"

            created_event = (
                service.events()
                .insert(
                    calendarId=self.calendar_id,
                    body=event_body,
                    sendUpdates=send_updates,
                )
                .execute()
            )

            formatted_date = start_dt.strftime("%A, %B %d, %Y")
            formatted_time = start_dt.strftime("%I:%M %p")
            end_formatted_time = end_dt.strftime("%I:%M %p")

            logger.info(
                f"Successfully created Google Calendar event: id={created_event.get('id')}, "
                f"summary='{summary}', start={start_dt.isoformat()}"
            )

            return {
                "status": "confirmed",
                "event_id": created_event.get("id"),
                "summary": created_event.get("summary"),
                "date": formatted_date,
                "start_time": formatted_time,
                "end_time": end_formatted_time,
                "timezone": tz_str,
                "html_link": created_event.get("htmlLink"),
                "message": (
                    f"Your appointment for {summary} is confirmed for {formatted_date} from "
                    f"{formatted_time} to {end_formatted_time}."
                ),
            }

        except HttpError as err:
            logger.error(f"Google Calendar API HTTP error in book_appointment: {err}")
            return {
                "status": "error",
                "error": err.reason,
                "message": "I was unable to save the appointment to the calendar due to a system error.",
            }
        except Exception as e:
            logger.error(f"Failed to book appointment: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": f"Failed to book appointment: {str(e)}",
            }

    def list_upcoming_events(self, max_results: int = 5) -> List[Dict[str, Any]]:
        """List upcoming scheduled events from now onwards."""
        if not self.is_configured():
            return []

        try:
            service = self.get_service()
            now_iso = datetime.now(self._tz).isoformat()

            events_result = (
                service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=now_iso,
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            return events_result.get("items", [])
        except Exception as e:
            logger.error(f"Failed to list upcoming events: {e}")
            return []


# Global default manager instance
default_calendar_manager = GoogleCalendarManager()


# Standalone diagnostic runner
if __name__ == "__main__":
    import sys
    print("\n--- Google Calendar Integration Diagnostic Check ---")
    manager = GoogleCalendarManager()
    print(f"Credentials File: {manager.credentials_path}")
    print(f"File Exists:     {manager.is_configured()}")
    print(f"Target Calendar: {manager.calendar_id}")
    print(f"Active Timezone: {manager.timezone_name}")

    if not manager.is_configured():
        print("\n[!] 'credentials.json' not found.")
        print("    To enable live Google Calendar booking:")
        print("    1. Create a Google Cloud Project & enable Google Calendar API.")
        print("    2. Create a Service Account and download the JSON key as 'credentials.json'.")
        print("    3. Share your calendar with the service account's client_email address.")
    else:
        try:
            print("\nAttempting connection to Google Calendar API...")
            service = manager.get_service()
            sa_email = "Unknown"
            if os.path.exists(manager.credentials_path):
                with open(manager.credentials_path, "r") as f:
                    sa_data = json.load(f)
                    sa_email = sa_data.get("client_email", "Unknown")

            print(f"[+] Connection successful!")
            print(f"[+] Service Account Email: {sa_email}")

            if manager.calendar_id == "primary":
                print("\n" + "=" * 65)
                print("[!] IMPORTANT NOTICE ABOUT GOOGLE_CALENDAR_ID='primary':")
                print(f"   When using a Service Account, 'primary' refers to the bot's")
                print(f"   own internal service calendar ({sa_email}),")
                print(f"   NOT your personal Gmail / Google Workspace calendar.")
                print(f"   To see booked appointments in YOUR Google Calendar:")
                print(f"   1. Open Google Calendar (https://calendar.google.com).")
                print(f"   2. Click the 3 dots next to your calendar -> Settings and sharing.")
                print(f"   3. Under 'Share with specific people', add:")
                print(f"      {sa_email}")
                print(f"      with permission: 'Make changes to events'.")
                print(f"   4. In your .env file, update:")
                print(f"      GOOGLE_CALENDAR_ID=your_email@gmail.com")
                print("=" * 65 + "\n")

            if "--test-book" in sys.argv:
                print("\n[!] Testing appointment booking...")
                tomorrow = (datetime.now(manager.timezone) + timedelta(days=1)).replace(hour=14, minute=0, second=0)
                test_start = tomorrow.strftime("%Y-%m-%dT%H:%M:%S")
                res = manager.book_appointment(
                    summary="Test Voice Receptionist Booking",
                    start_time=test_start,
                    duration_minutes=30,
                    description="Diagnostic test event booked via calendar_tool.py",
                )
                print(f"Result: {json.dumps(res, indent=2)}")
            else:
                events = manager.list_upcoming_events(max_results=5)
                print(f"[+] Found {len(events)} upcoming events on calendar '{manager.calendar_id}':")
                for ev in events:
                    start = ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date"))
                    print(f"    - {ev.get('summary', 'Untitled')} ({start})")
                print("\nTip: Run 'python calendar_tool.py --test-book' to insert a test appointment directly.")
        except Exception as err:
            print(f"[-] Connection failed: {err}")
