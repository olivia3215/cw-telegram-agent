# tests/test_task_log.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Unit tests for task execution logging functionality.
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from clock import clock
from db.task_log import (
    delete_old_logs,
    format_action_details,
    get_agent_cost_logs,
    get_conversation_cost_logs,
    get_global_cost_logs,
    get_logs_after_timestamp,
    get_task_logs,
    log_task_execution,
)


@pytest.fixture
def mock_db():
    """Mock database connection for testing."""
    with patch('db.task_log.get_db_connection') as mock_conn:
        mock_cursor = MagicMock()
        mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_cursor
        mock_conn.return_value.__enter__.return_value.commit = MagicMock()
        yield mock_conn, mock_cursor


class TestLogTaskExecution:
    """Tests for log_task_execution function."""
    
    def test_logs_successful_task(self, mock_db):
        """Test logging a successful task execution."""
        mock_conn, mock_cursor = mock_db
        
        log_task_execution(
            agent_telegram_id=123,
            channel_telegram_id=456,
            action_kind="send",
            action_details='{"text": "Hello"}',
            failure_message=None,
            task_identifier="task-123",
        )
        
        # Verify INSERT was called
        assert mock_cursor.execute.called
        call_args = mock_cursor.execute.call_args
        assert "INSERT INTO task_execution_log" in call_args[0][0]
        
        # Verify parameters
        params = call_args[0][1]
        assert params[1] == 123  # agent_telegram_id
        assert params[2] == 456  # channel_telegram_id
        assert params[3] == "send"  # action_kind
        assert params[4] == "task-123"  # task_identifier
        assert params[5] == '{"text": "Hello"}'  # action_details
        assert params[6] is None  # failure_message
        
        # Verify commit was called
        assert mock_conn.return_value.__enter__.return_value.commit.called
    
    def test_logs_failed_task(self, mock_db):
        """Test logging a failed task execution."""
        mock_conn, mock_cursor = mock_db
        
        log_task_execution(
            agent_telegram_id=123,
            channel_telegram_id=456,
            action_kind="think",
            action_details='{"text": "Processing"}',
            failure_message="Connection timeout",
            task_identifier="task-456",
        )
        
        # Verify INSERT was called with failure message
        call_args = mock_cursor.execute.call_args
        params = call_args[0][1]
        assert params[6] == "Connection timeout"  # failure_message
    
    def test_handles_database_error_gracefully(self, mock_db):
        """Test that database errors don't raise exceptions."""
        mock_conn, mock_cursor = mock_db
        mock_cursor.execute.side_effect = Exception("Database error")
        
        # Should not raise an exception
        log_task_execution(
            agent_telegram_id=123,
            channel_telegram_id=456,
            action_kind="send",
        )
    
    def test_uses_clock_for_timestamp(self, mock_db):
        """Test that log_task_execution uses clock.now for timestamps."""
        mock_conn, mock_cursor = mock_db
        
        with patch('db.task_log.clock') as mock_clock:
            test_time = datetime(2026, 2, 9, 12, 0, 0, tzinfo=UTC)
            mock_clock.now.return_value = test_time
            
            log_task_execution(
                agent_telegram_id=123,
                channel_telegram_id=456,
                action_kind="send",
            )
            
            # Verify clock.now was called
            mock_clock.now.assert_called_once_with(UTC)
            
            # Verify timestamp in parameters
            call_args = mock_cursor.execute.call_args
            params = call_args[0][1]
            assert params[0] == test_time


class TestGetTaskLogs:
    """Tests for get_task_logs function."""
    
    def test_retrieves_logs_within_date_range(self, mock_db):
        """Test retrieving logs from the past N days."""
        mock_conn, mock_cursor = mock_db
        
        # Mock database rows
        test_time = datetime(2026, 2, 9, 12, 0, 0, tzinfo=UTC)
        mock_cursor.fetchall.return_value = [
            {
                "id": 1,
                "timestamp": test_time,
                "action_kind": "send",
                "task_identifier": "task-1",
                "action_details": '{"text": "Hello"}',
                "failure_message": None,
            },
            {
                "id": 2,
                "timestamp": test_time - timedelta(hours=1),
                "action_kind": "think",
                "task_identifier": "task-2",
                "action_details": '{"text": "Processing"}',
                "failure_message": None,
            },
        ]
        
        logs = get_task_logs(
            agent_telegram_id=123,
            channel_telegram_id=456,
            days=7,
        )
        
        # Verify query was executed
        assert mock_cursor.execute.called
        call_args = mock_cursor.execute.call_args
        assert "SELECT id, timestamp, action_kind" in call_args[0][0]
        assert "WHERE agent_telegram_id = %s" in call_args[0][0]
        
        # Verify results
        assert len(logs) == 2
        assert logs[0]["id"] == 1
        assert logs[0]["action_kind"] == "send"
        assert logs[0]["task_identifier"] == "task-1"
        assert logs[1]["id"] == 2
        assert logs[1]["action_kind"] == "think"
    
    def test_handles_timezone_naive_timestamps(self, mock_db):
        """Test that timezone-naive timestamps are converted to UTC."""
        mock_conn, mock_cursor = mock_db
        
        # Mock row with timezone-naive datetime
        naive_time = datetime(2026, 2, 9, 12, 0, 0)  # No tzinfo
        mock_cursor.fetchall.return_value = [
            {
                "id": 1,
                "timestamp": naive_time,
                "action_kind": "send",
                "task_identifier": None,
                "action_details": None,
                "failure_message": None,
            }
        ]
        
        logs = get_task_logs(123, 456)
        
        # Verify timestamp was made timezone-aware
        assert logs[0]["timestamp"].endswith("+00:00") or logs[0]["timestamp"].endswith("Z")
    
    def test_returns_empty_list_on_error(self, mock_db):
        """Test that database errors return empty list."""
        mock_conn, mock_cursor = mock_db
        mock_cursor.execute.side_effect = Exception("Database error")
        
        logs = get_task_logs(123, 456)
        
        assert logs == []


class TestGetLogsAfterTimestamp:
    """Tests for get_logs_after_timestamp function."""
    
    def test_filters_by_timestamp(self, mock_db):
        """Test retrieving logs after a specific timestamp."""
        mock_conn, mock_cursor = mock_db
        
        cutoff_time = datetime(2026, 2, 9, 10, 0, 0, tzinfo=UTC)
        test_time = datetime(2026, 2, 9, 12, 0, 0, tzinfo=UTC)
        
        mock_cursor.fetchall.return_value = [
            {
                "id": 1,
                "timestamp": test_time,
                "action_kind": "send",
                "task_identifier": "task-1",
                "action_details": None,
                "failure_message": None,
            }
        ]
        
        logs = get_logs_after_timestamp(
            agent_telegram_id=123,
            channel_telegram_id=456,
            after_timestamp=cutoff_time,
        )
        
        # Verify query includes timestamp filter
        call_args = mock_cursor.execute.call_args
        assert "timestamp >=" in call_args[0][0]
        assert call_args[0][1][2] == cutoff_time
        
        # Verify results
        assert len(logs) == 1
        assert logs[0]["id"] == 1


class TestCostLogs:
    """Tests for llm_usage cost log retrieval helpers."""

    def test_get_conversation_cost_logs(self, mock_db):
        """Test conversation-scoped cost log retrieval and total calculation."""
        _, mock_cursor = mock_db
        test_time = datetime(2026, 2, 9, 12, 0, 0, tzinfo=UTC)
        mock_cursor.fetchall.return_value = [
            {
                "id": 1,
                "timestamp": test_time,
                "agent_telegram_id": 123,
                "channel_telegram_id": 456,
                "task_identifier": None,
                "action_details": json.dumps(
                    {
                        "operation": "query_structured",
                        "model_name": "gemini-3-flash-preview",
                        "input_tokens": 1000,
                        "output_tokens": 500,
                        "cost": 0.002,
                    }
                ),
            },
            {
                "id": 2,
                "timestamp": test_time - timedelta(minutes=1),
                "agent_telegram_id": 123,
                "channel_telegram_id": 456,
                "task_identifier": None,
                "action_details": json.dumps(
                    {
                        "operation": "describe_image",
                        "model_name": "gemini-3-flash-preview",
                        "input_tokens": 500,
                        "output_tokens": 100,
                        "cost": "$0.0010",
                    }
                ),
            },
        ]

        result = get_conversation_cost_logs(123, 456, days=7)
        assert len(result["logs"]) == 2
        assert result["total_cost"] == pytest.approx(0.003)
        assert result["logs"][0]["operation"] == "query_structured"

        call_args = mock_cursor.execute.call_args
        assert "action_kind = 'llm_usage'" in call_args[0][0]
        assert "agent_telegram_id = %s" in call_args[0][0]
        assert "channel_telegram_id = %s" in call_args[0][0]

    def test_get_agent_cost_logs(self, mock_db):
        """Test agent-scoped cost log retrieval."""
        _, mock_cursor = mock_db
        test_time = datetime(2026, 2, 9, 12, 0, 0, tzinfo=UTC)
        mock_cursor.fetchall.return_value = [
            {
                "id": 1,
                "timestamp": test_time,
                "agent_telegram_id": 123,
                "channel_telegram_id": 111,
                "task_identifier": None,
                "action_details": json.dumps({"cost": 0.0015}),
            }
        ]

        result = get_agent_cost_logs(123, days=7)
        assert len(result["logs"]) == 1
        assert result["total_cost"] == pytest.approx(0.0015)

        call_args = mock_cursor.execute.call_args
        assert "agent_telegram_id = %s" in call_args[0][0]
        assert "timestamp >= %s" in call_args[0][0]

    def test_get_global_cost_logs(self, mock_db):
        """Test global cost log retrieval."""
        _, mock_cursor = mock_db
        test_time = datetime(2026, 2, 9, 12, 0, 0, tzinfo=UTC)
        mock_cursor.fetchall.return_value = [
            {
                "id": 1,
                "timestamp": test_time,
                "agent_telegram_id": 123,
                "channel_telegram_id": 111,
                "task_identifier": None,
                "action_details": json.dumps({"cost": "$0.0025"}),
            }
        ]

        result = get_global_cost_logs(days=7)
        assert len(result["logs"]) == 1
        assert result["total_cost"] == pytest.approx(0.0025)

        call_args = mock_cursor.execute.call_args
        assert "timestamp >= %s" in call_args[0][0]


class TestDeleteOldLogs:
    """Tests for delete_old_logs function."""
    
    def test_deletes_logs_older_than_days(self, mock_db):
        """Test deleting logs older than N days."""
        mock_conn, mock_cursor = mock_db
        mock_cursor.rowcount = 42  # Mock deleted rows
        
        with patch('db.task_log.clock') as mock_clock:
            test_time = datetime(2026, 2, 9, 12, 0, 0, tzinfo=UTC)
            mock_clock.now.return_value = test_time
            
            deleted_count = delete_old_logs(days=14)
            
            # Verify DELETE query was executed
            assert mock_cursor.execute.called
            call_args = mock_cursor.execute.call_args
            assert "DELETE FROM task_execution_log" in call_args[0][0]
            assert "WHERE timestamp < %s" in call_args[0][0]
            
            # Verify cutoff time calculation
            expected_cutoff = test_time - timedelta(days=14)
            assert call_args[0][1][0] == expected_cutoff
            
            # Verify return value
            assert deleted_count == 42
    
    def test_returns_zero_on_error(self, mock_db):
        """Test that database errors return 0."""
        mock_conn, mock_cursor = mock_db
        mock_cursor.execute.side_effect = Exception("Database error")
        
        deleted_count = delete_old_logs()
        
        assert deleted_count == 0


class TestFormatActionDetails:
    """Tests for format_action_details function."""
    
    def test_excludes_blacklisted_parameters(self):
        """Test that blacklisted parameters are excluded."""
        params = {
            "text": "Hello",
            "silent": True,  # Blacklisted
            "previous_retries": 2,  # Blacklisted
            "callout": False,  # Blacklisted
        }
        
        result = format_action_details("send", params)
        parsed = json.loads(result)
        
        assert "text" in parsed
        assert "silent" not in parsed
        assert "previous_retries" not in parsed
        assert "callout" not in parsed
    
    def test_truncates_long_text_fields(self):
        """Test that long text fields are truncated."""
        long_text = "A" * 11000  # Exceeds 10000 char limit
        params = {"text": long_text}
        
        result = format_action_details("send", params)
        parsed = json.loads(result)
        
        assert len(parsed["text"]) == 10003  # 10000 + "..."
        assert parsed["text"].endswith("...")
    
    def test_truncates_content_field(self):
        """Test that content field is truncated."""
        long_content = "B" * 11000
        params = {"content": long_content}
        
        result = format_action_details("remember", params)
        parsed = json.loads(result)
        
        assert len(parsed["content"]) == 10003
        assert parsed["content"].endswith("...")
    
    def test_truncates_xsend_intent(self):
        """Test that xsend_intent field is truncated."""
        long_intent = "C" * 11000
        params = {"xsend_intent": long_intent}
        
        result = format_action_details("xsend", params)
        parsed = json.loads(result)
        
        assert len(parsed["xsend_intent"]) == 10003
        assert parsed["xsend_intent"].endswith("...")
    
    def test_truncates_caption_field(self):
        """Test that caption field is truncated to 10000 chars."""
        long_caption = "D" * 11000
        params = {"caption": long_caption}
        
        result = format_action_details("photo", params)
        parsed = json.loads(result)
        
        assert len(parsed["caption"]) == 10003  # 10000 + "..."
        assert parsed["caption"].endswith("...")
    
    def test_preserves_non_blacklisted_parameters(self):
        """Test that normal parameters are preserved."""
        params = {
            "text": "Hello",
            "channel_id": 123,
            "message_id": 456,
            "urls": ["https://example.com"],
        }
        
        result = format_action_details("send", params)
        parsed = json.loads(result)
        
        assert parsed["text"] == "Hello"
        assert parsed["channel_id"] == 123
        assert parsed["message_id"] == 456
        assert parsed["urls"] == ["https://example.com"]
    
    def test_returns_action_marker_for_empty_params(self):
        """Test that empty params return action marker."""
        params = {}
        
        result = format_action_details("wait", params)
        parsed = json.loads(result)
        
        assert parsed == {"action": "wait"}
    
    def test_returns_action_marker_when_only_blacklisted_params(self):
        """Test that only blacklisted params return action marker."""
        params = {
            "silent": True,
            "previous_retries": 2,
            "callout": False,
        }
        
        result = format_action_details("send", params)
        parsed = json.loads(result)
        
        assert parsed == {"action": "send"}
    
    def test_handles_complex_nested_data(self):
        """Test that complex nested data is properly serialized."""
        params = {
            "text": "Hello",
            "metadata": {
                "retry_count": 1,
                "delay": 5,
            },
            "tags": ["urgent", "important"],
        }
        
        result = format_action_details("send", params)
        parsed = json.loads(result)
        
        assert parsed["text"] == "Hello"
        assert parsed["metadata"]["retry_count"] == 1
        assert parsed["tags"] == ["urgent", "important"]


class TestTaskLogIntegration:
    """Integration tests for task logging workflow."""
    
    def test_log_and_retrieve_workflow(self, mock_db):
        """Test the complete workflow of logging and retrieving tasks."""
        mock_conn, mock_cursor = mock_db
        
        # Log a task
        test_time = datetime(2026, 2, 9, 12, 0, 0, tzinfo=UTC)
        with patch('db.task_log.clock') as mock_clock:
            mock_clock.now.return_value = test_time
            
            log_task_execution(
                agent_telegram_id=123,
                channel_telegram_id=456,
                action_kind="send",
                action_details='{"text": "Hello"}',
                task_identifier="task-123",
            )
        
        # Mock retrieval
        mock_cursor.fetchall.return_value = [
            {
                "id": 1,
                "timestamp": test_time,
                "action_kind": "send",
                "task_identifier": "task-123",
                "action_details": '{"text": "Hello"}',
                "failure_message": None,
            }
        ]
        
        # Retrieve logs
        logs = get_task_logs(123, 456, days=7)
        
        # Verify
        assert len(logs) == 1
        assert logs[0]["action_kind"] == "send"
        assert logs[0]["task_identifier"] == "task-123"
    
    def test_format_and_log_workflow(self, mock_db):
        """Test formatting action details and logging."""
        mock_conn, mock_cursor = mock_db
        
        # Format action details
        params = {
            "text": "Hello World",
            "channel_id": 456,
            "silent": True,  # Should be filtered
        }
        action_details = format_action_details("send", params)
        
        # Log with formatted details
        log_task_execution(
            agent_telegram_id=123,
            channel_telegram_id=456,
            action_kind="send",
            action_details=action_details,
        )
        
        # Verify the logged details don't contain blacklisted params
        call_args = mock_cursor.execute.call_args
        logged_details = call_args[0][1][5]  # action_details parameter
        parsed = json.loads(logged_details)
        
        assert "text" in parsed
        assert "channel_id" in parsed
        assert "silent" not in parsed
