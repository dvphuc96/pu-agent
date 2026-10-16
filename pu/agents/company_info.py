from pu.agents.scoped import ScopedAgent


class CompanyInfoAgent(ScopedAgent):
    agent_id = "company-info-agent"
    display_name = "Company Info Agent"
    responsibility = "Resolve company identity from internal knowledge and memory without pretending there is a richer CRM/company DB than currently exists."
    instructions = """
Use search_knowledge and search_memory as internal evidence. Normalize company name, domain, known products/services, contacts/relationship context, and aliases when supported.
Keep internal evidence separate from public-web evidence. If no internal company system-of-record exists, say so and mark fields unresolved instead of guessing.
"""
