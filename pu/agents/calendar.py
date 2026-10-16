from pu.agents.scoped import ScopedAgent


class CalendarAgent(ScopedAgent):
    agent_id = "calendar-agent"
    display_name = "Calendar Agent"
    responsibility = "Read upcoming calendar context and correlate meetings with the company/contact under research."
    instructions = """
Use list_events only. Identify relevant meetings, attendees/domains, meeting time, and preparation urgency.
Return: relevant_events, attendee_matches, time_to_meeting when inferable, and missing_fields.
This agent is read-only: never create, update, cancel, or propose calendar writes.
"""
