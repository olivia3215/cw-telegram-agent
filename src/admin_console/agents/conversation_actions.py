# admin_console/agents/conversation_actions.py
#
# Conversation action routes for the admin console.
# This module imports and registers routes from split modules.

from flask import Blueprint  # pyright: ignore[reportMissingImports]


def register_conversation_actions_routes(agents_bp: Blueprint):
    """Register conversation action routes."""
    # Import and register routes from split modules
    from admin_console.agents import (
        conversation_translate,
        conversation_xsend,
        conversation_summarize,
        conversation_delete_telepathic,
        conversation_download,
    )

    # Register all routes
    conversation_translate.register_conversation_translate_routes(agents_bp)
    conversation_xsend.register_conversation_xsend_routes(agents_bp)
    conversation_summarize.register_conversation_summarize_routes(agents_bp)
    conversation_delete_telepathic.register_conversation_delete_telepathic_routes(agents_bp)
    conversation_download.register_conversation_download_routes(agents_bp)


# Legacy route implementations moved to separate modules:
# - conversation_translate.py: translate route
# - conversation_xsend.py: xsend route
# - conversation_summarize.py: summarize route
# - conversation_delete_telepathic.py: delete-telepathic-messages route
# - conversation_download.py: download route
#
# The original implementation was removed to reduce file size. See the individual modules for route implementations.
