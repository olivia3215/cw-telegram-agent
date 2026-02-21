# tests/test_task_schema.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Tests for task schema filtering based on allowed task types.
"""

import pytest

from llm.task_schema import (
    extract_task_types_from_prompt,
    get_task_response_schema_dict,
)


def test_extract_task_types_from_prompt_single_task():
    """Test extracting a single task type from a prompt."""
    prompt = "<!-- SCHEMA_TASKS: send -->\n\n# Instructions\n\nSome text here."
    task_types = extract_task_types_from_prompt(prompt)
    assert task_types == {"send"}


def test_extract_task_types_from_prompt_multiple_tasks():
    """Test extracting multiple task types from a prompt."""
    prompt = "<!-- SCHEMA_TASKS: send, react, sticker -->\n\n# Instructions\n\nSome text."
    task_types = extract_task_types_from_prompt(prompt)
    assert task_types == {"send", "react", "sticker"}


def test_extract_task_types_from_prompt_multiple_comments():
    """Test extracting task types from multiple SCHEMA_TASKS comments."""
    prompt = """<!-- SCHEMA_TASKS: send, react -->
# Some section

<!-- SCHEMA_TASKS: sticker, wait -->
# Another section
"""
    task_types = extract_task_types_from_prompt(prompt)
    assert task_types == {"send", "react", "sticker", "wait"}


def test_extract_task_types_from_prompt_case_insensitive_comment():
    """Test that SCHEMA_TASKS comment matching is case insensitive (but task names preserve case)."""
    prompt = "<!-- schema_tasks: send, react -->\n\n# Instructions"
    task_types = extract_task_types_from_prompt(prompt)
    assert task_types == {"send", "react"}


def test_extract_task_types_from_prompt_no_comment():
    """Test extracting from a prompt with no SCHEMA_TASKS comment."""
    prompt = "# Instructions\n\nSome text here."
    task_types = extract_task_types_from_prompt(prompt)
    assert task_types == set()


def test_extract_task_types_from_prompt_whitespace_handling():
    """Test that whitespace in task lists is handled correctly."""
    prompt = "<!-- SCHEMA_TASKS: send , react , sticker -->\n\n# Instructions"
    task_types = extract_task_types_from_prompt(prompt)
    assert task_types == {"send", "react", "sticker"}


def test_get_task_response_schema_dict_no_filtering():
    """Test that get_task_response_schema_dict returns full schema when None is passed."""
    schema = get_task_response_schema_dict(allowed_task_types=None)
    
    # Should have all task types
    items = schema["items"]
    assert "anyOf" in items
    task_kinds = set()
    for task_schema in items["anyOf"]:
        kind_enum = task_schema.get("properties", {}).get("kind", {}).get("enum", [])
        if kind_enum:
            task_kinds.add(kind_enum[0])
    
    # Should include all task types
    expected_tasks = {
        "think", "send", "react", "sticker", "send_media", "wait",
        "block", "unblock", "remember", "intend", "plan", "note",
        "summarize", "retrieve", "xsend", "schedule"
    }
    assert task_kinds == expected_tasks


def test_get_task_response_schema_dict_filtering_single_task():
    """Test filtering schema to include only a single task type."""
    schema = get_task_response_schema_dict(allowed_task_types={"send"})
    
    items = schema["items"]
    assert "anyOf" in items
    
    # Should only have send task
    task_kinds = set()
    for task_schema in items["anyOf"]:
        kind_enum = task_schema.get("properties", {}).get("kind", {}).get("enum", [])
        if kind_enum:
            task_kinds.add(kind_enum[0])
    
    assert task_kinds == {"send"}


def test_get_task_response_schema_dict_filtering_multiple_tasks():
    """Test filtering schema to include multiple task types."""
    schema = get_task_response_schema_dict(allowed_task_types={"send", "react", "think"})
    
    items = schema["items"]
    assert "anyOf" in items
    
    # Should only have the specified tasks
    task_kinds = set()
    for task_schema in items["anyOf"]:
        kind_enum = task_schema.get("properties", {}).get("kind", {}).get("enum", [])
        if kind_enum:
            task_kinds.add(kind_enum[0])
    
    assert task_kinds == {"send", "react", "think"}


def test_get_task_response_schema_dict_filtering_empty_set():
    """Test filtering schema with an empty set of allowed tasks."""
    schema = get_task_response_schema_dict(allowed_task_types=set())
    
    items = schema["items"]
    assert "anyOf" in items
    
    # Should have no task types
    task_kinds = set()
    for task_schema in items["anyOf"]:
        kind_enum = task_schema.get("properties", {}).get("kind", {}).get("enum", [])
        if kind_enum:
            task_kinds.add(kind_enum[0])
    
    assert task_kinds == set()


def test_get_task_response_schema_dict_filtering_unknown_task():
    """Test that unknown task types are ignored when filtering."""
    schema = get_task_response_schema_dict(allowed_task_types={"send", "nonexistent_task"})
    
    items = schema["items"]
    assert "anyOf" in items
    
    # Should only have send (nonexistent_task should be ignored)
    task_kinds = set()
    for task_schema in items["anyOf"]:
        kind_enum = task_schema.get("properties", {}).get("kind", {}).get("enum", [])
        if kind_enum:
            task_kinds.add(kind_enum[0])
    
    assert task_kinds == {"send"}


def test_get_task_response_schema_dict_filtering_all_common_tasks():
    """Test filtering with all common tasks from Instructions.md."""
    allowed = {"send", "react", "sticker", "wait", "block", "unblock", "think"}
    schema = get_task_response_schema_dict(allowed_task_types=allowed)
    
    items = schema["items"]
    assert "anyOf" in items
    
    task_kinds = set()
    for task_schema in items["anyOf"]:
        kind_enum = task_schema.get("properties", {}).get("kind", {}).get("enum", [])
        if kind_enum:
            task_kinds.add(kind_enum[0])
    
    assert task_kinds == allowed


def test_get_task_response_schema_dict_preserves_schema_structure():
    """Test that filtering preserves the overall schema structure."""
    schema = get_task_response_schema_dict(allowed_task_types={"send"})
    
    # Should still have the same top-level structure
    assert schema["type"] == "array"
    assert "title" in schema
    assert "description" in schema
    assert "items" in schema
    assert "anyOf" in schema["items"]
    
    # Should have exactly one task schema
    assert len(schema["items"]["anyOf"]) == 1
    
    # That task should be send
    send_schema = schema["items"]["anyOf"][0]
    assert send_schema["title"] == "Send Task"
    assert send_schema["properties"]["kind"]["enum"] == ["send"]


def test_get_task_response_schema_dict_independent_copies():
    """Test that filtering returns independent copies (no shared mutation)."""
    schema1 = get_task_response_schema_dict(allowed_task_types={"send"})
    schema2 = get_task_response_schema_dict(allowed_task_types={"react"})
    
    # Should be independent - filtering one shouldn't affect the other
    items1 = schema1["items"]["anyOf"]
    items2 = schema2["items"]["anyOf"]
    
    assert len(items1) == 1
    assert len(items2) == 1
    assert items1[0]["properties"]["kind"]["enum"] == ["send"]
    assert items2[0]["properties"]["kind"]["enum"] == ["react"]


def test_get_task_response_schema_dict_multiple_calls_same_filter():
    """Test that multiple calls with the same filter return equivalent results."""
    schema1 = get_task_response_schema_dict(allowed_task_types={"send", "react"})
    schema2 = get_task_response_schema_dict(allowed_task_types={"send", "react"})
    
    # Should have the same task types
    items1 = schema1["items"]["anyOf"]
    items2 = schema2["items"]["anyOf"]
    
    kinds1 = {item["properties"]["kind"]["enum"][0] for item in items1}
    kinds2 = {item["properties"]["kind"]["enum"][0] for item in items2}
    
    assert kinds1 == kinds2 == {"send", "react"}

