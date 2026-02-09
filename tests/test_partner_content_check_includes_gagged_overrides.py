from unittest.mock import MagicMock, patch

from admin_console.app import create_admin_app


def _make_client():
    # create_admin_app() scans media directories on startup, which can require MySQL
    # configuration for media sources. For this unit test, we don't need media sources.
    with patch("admin_console.app.scan_media_directories", return_value=[]):
        app = create_admin_app()
    app.testing = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["admin_console_verified"] = True
    return client


def test_partner_content_check_marks_conversation_parameters_when_gagged_override_present():
    client = _make_client()

    agent = MagicMock()
    agent.agent_id = 123

    mock_work_queue = MagicMock()
    mock_work_queue.graph_for_conversation.return_value = None

    with patch("admin_console.agents.memory.get_agent_by_name", return_value=agent), patch(
        "db.conversation_llm.channels_with_conversation_llm_overrides",
        return_value=set(),
    ), patch(
        "db.conversation_gagged.channels_with_conversation_gagged_overrides",
        return_value={12345},
    ), patch(
        "db.notes.load_notes",
        return_value=[],
    ), patch(
        "db.plans.load_plans",
        return_value=[],
    ), patch(
        "task_graph.WorkQueue.get_instance",
        return_value=mock_work_queue,
    ):
        resp = client.post(
            "/admin/api/agents/TestAgent/partner-content-check",
            json={"user_ids": ["12345"]},
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data and "content_checks" in data
    assert data["content_checks"]["12345"]["conversation_parameters"] is True
    assert data["content_checks"]["12345"]["work_queue"] is False


def test_partner_content_check_includes_work_queue():
    """Test that partner-content-check includes work_queue field."""
    client = _make_client()

    agent = MagicMock()
    agent.agent_id = 123

    # Mock a graph with tasks
    mock_graph = MagicMock()
    mock_graph.tasks = [MagicMock(), MagicMock()]  # Two tasks

    mock_work_queue = MagicMock()
    mock_work_queue.graph_for_conversation.return_value = mock_graph

    with patch("admin_console.agents.memory.get_agent_by_name", return_value=agent), patch(
        "db.conversation_llm.channels_with_conversation_llm_overrides",
        return_value=set(),
    ), patch(
        "db.conversation_gagged.channels_with_conversation_gagged_overrides",
        return_value=set(),
    ), patch(
        "db.notes.load_notes",
        return_value=[],
    ), patch(
        "db.plans.load_plans",
        return_value=[],
    ), patch(
        "task_graph.WorkQueue.get_instance",
        return_value=mock_work_queue,
    ):
        resp = client.post(
            "/admin/api/agents/TestAgent/partner-content-check",
            json={"user_ids": ["12345"]},
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data and "content_checks" in data
    assert data["content_checks"]["12345"]["work_queue"] is True
    mock_work_queue.graph_for_conversation.assert_called_once_with(123, 12345)


def test_partner_content_check_work_queue_false_when_no_graph():
    """Test that work_queue is False when no graph exists."""
    client = _make_client()

    agent = MagicMock()
    agent.agent_id = 123

    mock_work_queue = MagicMock()
    mock_work_queue.graph_for_conversation.return_value = None

    with patch("admin_console.agents.memory.get_agent_by_name", return_value=agent), patch(
        "db.conversation_llm.channels_with_conversation_llm_overrides",
        return_value=set(),
    ), patch(
        "db.conversation_gagged.channels_with_conversation_gagged_overrides",
        return_value=set(),
    ), patch(
        "db.notes.load_notes",
        return_value=[],
    ), patch(
        "db.plans.load_plans",
        return_value=[],
    ), patch(
        "task_graph.WorkQueue.get_instance",
        return_value=mock_work_queue,
    ):
        resp = client.post(
            "/admin/api/agents/TestAgent/partner-content-check",
            json={"user_ids": ["12345"]},
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data and "content_checks" in data
    assert data["content_checks"]["12345"]["work_queue"] is False


def test_partner_content_check_work_queue_false_when_empty_graph():
    """Test that work_queue is False when graph has no tasks."""
    client = _make_client()

    agent = MagicMock()
    agent.agent_id = 123

    # Mock a graph with no tasks
    mock_graph = MagicMock()
    mock_graph.tasks = []

    mock_work_queue = MagicMock()
    mock_work_queue.graph_for_conversation.return_value = mock_graph

    with patch("admin_console.agents.memory.get_agent_by_name", return_value=agent), patch(
        "db.conversation_llm.channels_with_conversation_llm_overrides",
        return_value=set(),
    ), patch(
        "db.conversation_gagged.channels_with_conversation_gagged_overrides",
        return_value=set(),
    ), patch(
        "db.notes.load_notes",
        return_value=[],
    ), patch(
        "db.plans.load_plans",
        return_value=[],
    ), patch(
        "task_graph.WorkQueue.get_instance",
        return_value=mock_work_queue,
    ):
        resp = client.post(
            "/admin/api/agents/TestAgent/partner-content-check",
            json={"user_ids": ["12345"]},
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data and "content_checks" in data
    assert data["content_checks"]["12345"]["work_queue"] is False

