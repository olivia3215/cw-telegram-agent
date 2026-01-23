# admin_console/agents/conversation_actions.py
#
# Conversation action routes for the admin console.
# This module imports and registers routes from split modules.

from flask import Blueprint  # pyright: ignore[reportMissingImports]


def register_conversation_actions_routes(agents_bp: Blueprint):
    """Register conversation action routes."""
    # Import and register routes from split modules
    # Use importlib to avoid circular import issues
    import importlib.util
    from pathlib import Path
    
    agents_dir = Path(__file__).parent
    
    # Load conversation_translate
    translate_path = agents_dir / "conversation_translate.py"
    translate_spec = importlib.util.spec_from_file_location("conversation_translate", translate_path)
    conversation_translate = importlib.util.module_from_spec(translate_spec)
    translate_spec.loader.exec_module(conversation_translate)
    
    # Load conversation_xsend
    xsend_path = agents_dir / "conversation_xsend.py"
    xsend_spec = importlib.util.spec_from_file_location("conversation_xsend", xsend_path)
    conversation_xsend = importlib.util.module_from_spec(xsend_spec)
    xsend_spec.loader.exec_module(conversation_xsend)
    
    # Load conversation_summarize
    summarize_path = agents_dir / "conversation_summarize.py"
    summarize_spec = importlib.util.spec_from_file_location("conversation_summarize", summarize_path)
    conversation_summarize = importlib.util.module_from_spec(summarize_spec)
    summarize_spec.loader.exec_module(conversation_summarize)
    
    # Load conversation_delete_telepathic
    delete_telepathic_path = agents_dir / "conversation_delete_telepathic.py"
    delete_telepathic_spec = importlib.util.spec_from_file_location("conversation_delete_telepathic", delete_telepathic_path)
    conversation_delete_telepathic = importlib.util.module_from_spec(delete_telepathic_spec)
    delete_telepathic_spec.loader.exec_module(conversation_delete_telepathic)
    
    # Load conversation_download
    download_path = agents_dir / "conversation_download.py"
    download_spec = importlib.util.spec_from_file_location("conversation_download", download_path)
    conversation_download = importlib.util.module_from_spec(download_spec)
    download_spec.loader.exec_module(conversation_download)
    
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
