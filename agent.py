"""
Production Voice AI Receptionist
================================
Real-time, low-latency (<500ms) Voice AI Receptionist using:
- LiveKit Agents SDK: Real-time WebRTC audio transport, Silero VAD, interruption handling (barge-in)
- Deepgram Nova-2: Real-time streaming Speech-to-Text (STT)
- Groq LPU (openai/gpt-oss-120b or llama-3.3-70b-versatile): Ultra-fast LLM inference via OpenAI-compatible endpoint
- Cartesia Sonic: Ultra-low latency, expressive Text-to-Speech (TTS)
- Google Calendar API: Automated appointment booking & availability verification
"""

import os
import sys
import json
import asyncio
import logging
from datetime import datetime
from typing import Annotated, Optional
from dotenv import load_dotenv

# Ensure UTF-8 output on Windows console to prevent charmap encoding errors
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# LiveKit Agents Core & Pipeline
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    llm,
)
from livekit.agents.pipeline import VoicePipelineAgent, AgentCallContext
from livekit.plugins import cartesia, deepgram, openai, silero

# Google Calendar Integration Tool
from calendar_tool import GoogleCalendarManager

# Load environment configuration from .env
load_dotenv()

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("voice-receptionist")


# ------------------------------------------------------------------------------
# Receptionist System Instructions & Dynamic Prompting
# ------------------------------------------------------------------------------
def build_receptionist_prompt(calendar_manager: GoogleCalendarManager) -> str:
    """
    Construct dynamic system instructions with accurate current date, time,
    and timezone context for reliable natural language time resolution.
    """
    from datetime import timedelta
    now = datetime.now(calendar_manager.timezone)
    tomorrow = now + timedelta(days=1)
    day_after = now + timedelta(days=2)
    tz_name = calendar_manager.timezone_name

    current_date_str = now.strftime("%A, %B %d, %Y")
    current_time_str = now.strftime("%I:%M %p")
    tomorrow_str = tomorrow.strftime("%A, %B %d, %Y")
    day_after_str = day_after.strftime("%A, %B %d, %Y")

    return f"""You are Sarah, an ultra-fast, professional Voice AI Receptionist for Apex Wellness Clinic.
You communicate live with callers over real-time telephone audio.

EXACT CALENDAR DATES FOR REFERENCE:
- Today is: {current_date_str} (Date ISO: {now.strftime("%Y-%m-%d")})
- Tomorrow is: {tomorrow_str} (Date ISO: {tomorrow.strftime("%Y-%m-%d")})
- Day after tomorrow is: {day_after_str} (Date ISO: {day_after.strftime("%Y-%m-%d")})
- Current Local Time: {current_time_str} ({tz_name})

PAST-TIME INTELLIGENCE:
- Current time is {current_time_str}. Any time earlier than {current_time_str} today has ALREADY PASSED.
- If a caller requests a time that has already passed today (e.g. they say "at 4 PM" or "at 10 AM" without specifying a day), you MUST schedule them for TOMORROW ({tomorrow_str}, ISO date: {tomorrow.strftime("%Y-%m-%d")})!

CRITICAL VOICE & SPEED RULES:
1. ULTRA-CONCISE: Limit spoken replies to 1 short sentence (maximum 15-20 words). Speak briskly and clearly.
2. NO MARKDOWN: Never use asterisks, bullet points, emojis, bold text, or URLs.
3. PRONOUNCE NATURALLY: Pronounce dates and times conversationally (e.g. say "Monday at 4 PM", never raw ISO timestamps).

ACTION-FIRST BOOKING PROTOCOL (MANDATORY):
- Whenever a caller asks to book an appointment (e.g., "Book me a slot for 4 PM" or "Schedule me for tomorrow at 4 PM"):
  --> IMMEDIATELY CALL the `book_appointment` tool!
  - For future times today, use today's date ({now.strftime("%Y-%m-%d")}).
  - For times that have already passed today, use tomorrow's date ({tomorrow.strftime("%Y-%m-%d")}).
  - If you know their name, pass it in summary/caller_name. If not, call `book_appointment` immediately with default summary.
  - DO NOT ask 4 follow-up questions before booking. Book first, then confirm!
  - NEVER say "I have booked your appointment" without executing the `book_appointment` tool.
- If the caller only asks "Are you open/free at 4 PM?", call `check_availability`. If available, reply: "Yes, 4 PM is open! Would you like me to book it?"
- When `book_appointment` returns confirmed, say: "I have booked your appointment for [Day] at [Time]! May I have your name for the file?"
"""


# ------------------------------------------------------------------------------
# Google Calendar Function Calling Tool Definition
# ------------------------------------------------------------------------------
class ReceptionistCalendarFunctions(llm.FunctionContext):
    """
    Function calling context exposing Google Calendar operations to Groq LLM.
    """

    def __init__(self, calendar_manager: GoogleCalendarManager):
        super().__init__()
        self.calendar_manager = calendar_manager

    @llm.ai_callable(
        description="Check if a specific date and time slot is open and available on the calendar before booking."
    )
    async def check_availability(
        self,
        start_time: Annotated[
            str,
            llm.TypeInfo(
                description="Start date and time in ISO 8601 format (YYYY-MM-DDTHH:MM:SS), e.g. '2026-10-27T14:00:00'."
            ),
        ],
        duration_minutes: Annotated[
            int,
            llm.TypeInfo(
                description="Expected duration of the appointment in minutes. Defaults to 30."
            ),
        ] = 30,
    ) -> str:
        """Inspects calendar for any conflicting events during the requested slot."""
        print(f"\n[TOOL CALL] check_availability invoked: start={start_time}, duration={duration_minutes}m", flush=True)
        logger.info(f"[Tool Call] check_availability: start={start_time}, duration={duration_minutes}m")
        result = await asyncio.to_thread(
            self.calendar_manager.check_availability,
            start_time=start_time,
            duration_minutes=duration_minutes,
        )
        print(f"[TOOL RESULT] Available={result.get('available')} | {result.get('message', '')}\n", flush=True)
        return json.dumps(result)

    @llm.ai_callable(
        description="Book an appointment directly into Google Calendar. Call this immediately when the caller requests, confirms, or agrees to schedule an appointment."
    )
    async def book_appointment(
        self,
        start_time: Annotated[
            str,
            llm.TypeInfo(
                description="Start date and time in ISO 8601 format (YYYY-MM-DDTHH:MM:SS), e.g. '2026-10-26T16:00:00'."
            ),
        ],
        summary: Annotated[
            str,
            llm.TypeInfo(
                description="Title or purpose of the appointment. Defaults to 'Appointment - Caller'."
            ),
        ] = "Appointment - Caller",
        duration_minutes: Annotated[
            int,
            llm.TypeInfo(
                description="Duration of appointment in minutes (standard is 30 or 60)."
            ),
        ] = 30,
        caller_name: Annotated[
            str,
            llm.TypeInfo(
                description="Full name of the caller or patient."
            ),
        ] = "",
        caller_phone: Annotated[
            str,
            llm.TypeInfo(
                description="Callback phone number of the caller."
            ),
        ] = "",
        attendee_email: Annotated[
            str,
            llm.TypeInfo(
                description="Email address of the caller to receive calendar invitations."
            ),
        ] = "",
        notes: Annotated[
            str,
            llm.TypeInfo(
                description="Additional notes, symptoms, or preferences mentioned by the caller."
            ),
        ] = "",
    ) -> str:
        """Inserts an event into the configured Google Calendar."""
        print("\n" + "=" * 60, flush=True)
        print(f"[TOOL CALL] book_appointment invoked!", flush=True)
        print(f"   Summary:     {summary}", flush=True)
        print(f"   Start Time:  {start_time}", flush=True)
        print(f"   Duration:    {duration_minutes} min", flush=True)
        print(f"   Caller:      {caller_name} (Phone: {caller_phone}, Email: {attendee_email})", flush=True)
        print(f"   Target Cal:  {self.calendar_manager.calendar_id}", flush=True)
        print("=" * 60, flush=True)

        logger.info(f"[Tool Call] book_appointment: summary='{summary}', start={start_time}, caller='{caller_name}'")

        # 1. Provide immediate vocal feedback to eliminate dead air during booking
        try:
            call_ctx = AgentCallContext.get_current()
            if call_ctx and call_ctx.agent:
                await call_ctx.agent.say("One moment, booking that for you now...", allow_interruptions=False)
        except Exception:
            pass

        desc_items = []
        if caller_name:
            desc_items.append(f"Client/Patient: {caller_name}")
        if caller_phone:
            desc_items.append(f"Phone: {caller_phone}")
        if attendee_email:
            desc_items.append(f"Email: {attendee_email}")
        if notes:
            desc_items.append(f"Notes: {notes}")
        description = "\n".join(desc_items) if desc_items else "Booked via AI Voice Receptionist."

        # 2. Run blocking Google Calendar API in a background thread to prevent WebRTC stutter
        result = await asyncio.to_thread(
            self.calendar_manager.book_appointment,
            summary=summary,
            start_time=start_time,
            duration_minutes=duration_minutes,
            description=description,
            attendee_email=attendee_email,
        )

        print(f"[TOOL RESULT] Status={result.get('status')} | {result.get('message', '')}\n", flush=True)
        return json.dumps(result)


# ------------------------------------------------------------------------------
# Prewarm & Performance Optimization
# ------------------------------------------------------------------------------
def prewarm(proc: JobProcess):
    """
    Preload Silero VAD into process memory before room connections arrive,
    eliminating cold-start latency on incoming calls.
    """
    logger.info("Prewarming Silero Voice Activity Detector (VAD)...")
    proc.userdata["vad"] = silero.VAD.load()
    logger.info("Silero VAD successfully prewarmed.")


# ------------------------------------------------------------------------------
# Main Agent Room Entrypoint
# ------------------------------------------------------------------------------
async def entrypoint(ctx: JobContext):
    """
    Worker entrypoint invoked whenever a user connects to a LiveKit room.
    Initializes STT, Groq LLM, Cartesia TTS, VAD, and VoicePipelineAgent.
    """
    logger.info(f"Connecting to LiveKit room: {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Wait for the remote caller participant
    participant = await ctx.wait_for_participant()
    logger.info(f"Caller participant joined: {participant.identity} (name: {participant.name})")

    # 1. Initialize Calendar Manager & Tools (pre-warm in background thread)
    calendar_mgr = GoogleCalendarManager()
    fnc_ctx = ReceptionistCalendarFunctions(calendar_mgr)
    asyncio.create_task(asyncio.to_thread(calendar_mgr.get_service))

    # 2. Build Dynamic Receptionist System Prompt
    system_prompt = build_receptionist_prompt(calendar_mgr)
    initial_chat_ctx = llm.ChatContext()
    initial_chat_ctx.append(
        role="system",
        text=system_prompt,
    )

    # 3. Speech-to-Text (STT): Deepgram Streaming Nova-2
    stt_plugin = deepgram.STT(
        model="nova-2",
        language="en-US",
        punctuate=True,
        smart_format=True,
        interim_results=True,
    )

    # 4. AI Brain (LLM): Groq LPU via OpenAI-Compatible Endpoint
    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        logger.warning("[!] GROQ_API_KEY is not set. Inference calls will fail.")

    groq_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

    llm_plugin = openai.LLM(
        model=groq_model,
        base_url="https://api.groq.com/openai/v1",
        api_key=groq_api_key,
        temperature=0.1,
    )

    # 5. Text-to-Speech (TTS): Cartesia Sonic Ultra-Low Latency
    cartesia_api_key = os.getenv("CARTESIA_API_KEY")
    if not cartesia_api_key:
        logger.warning("[!] CARTESIA_API_KEY is not set. Voice synthesis will fail.")

    cartesia_voice_id = os.getenv("CARTESIA_VOICE_ID", "f786b574-daa5-4673-aa0c-cbe3e8534c02")
    cartesia_model = os.getenv("CARTESIA_MODEL", "sonic-3")

    tts_plugin = cartesia.TTS(
        model=cartesia_model,
        voice=cartesia_voice_id,
        api_key=cartesia_api_key,
    )

    # 6. Retrieve Preloaded VAD
    vad_plugin = ctx.proc.userdata.get("vad") or silero.VAD.load()

    # 7. Core VoicePipelineAgent Orchestration
    # Configured for under-500ms conversational turn-around and barge-in
    agent = VoicePipelineAgent(
        vad=vad_plugin,
        stt=stt_plugin,
        llm=llm_plugin,
        tts=tts_plugin,
        chat_ctx=initial_chat_ctx,
        fnc_ctx=fnc_ctx,
        allow_interruptions=True,       # Instant barge-in: truncates TTS when user speaks
        min_endpointing_delay=0.3,      # Responsive 300ms turn endpointing
    )

    # Attach agent to room and participant
    agent.start(ctx.room, participant)
    logger.info("VoicePipelineAgent started. Live bi-directional audio active.")

    # Live event diagnostics for real-time visibility in terminal
    @agent.on("user_speech_committed")
    def _on_user_speech(msg: llm.ChatMessage):
        print(f"\n[USER HEARD] \"{msg.content}\"", flush=True)

    @agent.on("agent_speech_committed")
    def _on_agent_speech(msg: llm.ChatMessage):
        print(f"[AGENT SPOKE] \"{msg.content}\"", flush=True)

    @agent.on("function_calls_collected")
    def _on_fnc_collected(fncs):
        for f in fncs:
            print(f"[TOOL TRIGGERED] {f.function_info.name} (args: {f.raw_arguments})", flush=True)

    @agent.on("function_calls_finished")
    def _on_fnc_finished(called_fncs):
        for f in called_fncs:
            print(f"[TOOL COMPLETED] {f.call_info.function_info.name}", flush=True)

    # 8. Deliver Immediate Warm Greeting
    greeting_text = (
        "Hello! Thank you for calling Apex Wellness. My name is Sarah. "
        "How can I assist you with scheduling today?"
    )
    logger.info(f"Delivering opening greeting: '{greeting_text}'")
    await agent.say(greeting_text, allow_interruptions=True)


# ------------------------------------------------------------------------------
# Worker Process CLI Runner
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    # Validate critical environment keys before starting worker
    missing_vars = [
        var for var in ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "GROQ_API_KEY"]
        if not os.getenv(var)
    ]
    if missing_vars:
        logger.warning(
            f"[!] Warning: The following environment variables are missing: {', '.join(missing_vars)}. "
            "Please configure your .env file."
        )

    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        )
    )
