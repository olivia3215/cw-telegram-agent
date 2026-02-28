# src/admin_console/agents/__init__.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from flask import Blueprint  # pyright: ignore[reportMissingImports]

# Create agents blueprint
agents_bp = Blueprint("agents", __name__)

# Import and register all submodule routes using normal imports
from admin_console.agents import (
    costs,
    contacts,
    configuration,
    conversation,
    conversation_llm,
    events,
    intentions,
    login,
    media,
    memory,
    memberships,
    partners,
    plans,
    profile,
    routes,
    schedule,
    summaries,
)

# Register all routes
configuration.register_configuration_routes(agents_bp)
costs.register_cost_routes(agents_bp)
contacts.register_contact_routes(agents_bp)
conversation.register_conversation_routes(agents_bp)
conversation_llm.register_conversation_llm_routes(agents_bp)
events.register_event_routes(agents_bp)
intentions.register_intention_routes(agents_bp)
login.register_login_routes(agents_bp)
media.register_media_routes(agents_bp)
memory.register_memory_routes(agents_bp)
memberships.register_membership_routes(agents_bp)
partners.register_partner_routes(agents_bp)
plans.register_plan_routes(agents_bp)
profile.register_profile_routes(agents_bp)
routes.register_main_routes(agents_bp)
schedule.register_schedule_routes(agents_bp)
summaries.register_summary_routes(agents_bp)
