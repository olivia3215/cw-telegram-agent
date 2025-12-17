# admin_console/agents/conversation_content.py
#
# Conversation content retrieval routes for the admin console.

from flask import Blueprint  # pyright: ignore[reportMissingImports]


def register_conversation_content_routes(agents_bp: Blueprint):
    """Register conversation content retrieval routes."""
    # Use importlib to avoid relative import issues when loaded via importlib
    import importlib.util
    from pathlib import Path
    
    agents_dir = Path(__file__).parent
    
    # Load conversation_content_check
    check_path = agents_dir / "conversation_content_check.py"
    check_spec = importlib.util.spec_from_file_location("conversation_content_check", check_path)
    conversation_content_check = importlib.util.module_from_spec(check_spec)
    check_spec.loader.exec_module(conversation_content_check)
    
    # Load conversation_get
    get_path = agents_dir / "conversation_get.py"
    get_spec = importlib.util.spec_from_file_location("conversation_get", get_path)
    conversation_get = importlib.util.module_from_spec(get_spec)
    get_spec.loader.exec_module(conversation_get)
    
    agents_bp.route("/api/agents/<agent_config_name>/conversation-content-check", methods=["POST"])(
        conversation_content_check.api_check_conversation_content_batch
    )
    agents_bp.route("/api/agents/<agent_config_name>/conversation/<user_id>", methods=["GET"])(
        conversation_get.api_get_conversation
    )
