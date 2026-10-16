"""Specialized PU agents and shared agent abstractions."""

from pu.core.interfaces.agents import BaseAgent
from pu.agents.scoped import AgentDescriptor, ScopedAgent
from pu.agents.email import EmailAgent
from pu.agents.web_research import WebResearchAgent
from pu.agents.company_info import CompanyInfoAgent
from pu.agents.calendar import CalendarAgent
from pu.agents.memory import MemoryAgent
from pu.agents.report import ReportGenerationAgent

__all__ = [
    "BaseAgent",
    "AgentDescriptor",
    "ScopedAgent",
    "EmailAgent",
    "WebResearchAgent",
    "CompanyInfoAgent",
    "CalendarAgent",
    "MemoryAgent",
    "ReportGenerationAgent",
]
