from pu.agents.scoped import ScopedAgent


class MemoryAgent(ScopedAgent):
    agent_id = "memory-agent"
    display_name = "Memory Agent"
    responsibility = "Retrieve historical context relevant to the company, contact, or topic without mutating long-term memory."
    instructions = """
Use search_memory for semantic facts and episodic history. Return only relevant historical context with clear distinction between durable facts and past episodes.
Do not update, delete, consolidate, or create memory; this agent is read-only.
"""
