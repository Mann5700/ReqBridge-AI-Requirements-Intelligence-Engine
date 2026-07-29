"""Tests for the traceability graph module."""

from backend.app.graph.traceability import TraceabilityGraph


def test_build_graph_from_links():
    """Test graph construction from link dicts."""
    graph = TraceabilityGraph()
    links = [
        {
            "source_node_type": "chunk",
            "source_node_id": "chunk-001",
            "target_node_type": "requirement",
            "target_node_id": "req-001",
            "link_type": "derived_from",
            "confidence": 0.95,
        },
        {
            "source_node_type": "requirement",
            "source_node_id": "req-001",
            "target_node_type": "work_item",
            "target_node_id": "wi-001",
            "link_type": "decomposes_to",
            "confidence": 1.0,
        },
    ]

    graph.build_from_links(links)
    data = graph.to_json()

    assert len(data["nodes"]) == 3
    assert len(data["edges"]) == 2


def test_impact_analysis():
    """Test downstream impact analysis."""
    graph = TraceabilityGraph()
    graph.add_node("req-001", "requirement", "Requirement 1")
    graph.add_node("wi-001", "work_item", "Story 1")
    graph.add_node("wi-002", "work_item", "Task 1")
    graph.add_edge("req-001", "wi-001", "decomposes_to")
    graph.add_edge("wi-001", "wi-002", "parent_of")

    downstream = graph.get_impact_downstream("req-001")
    assert "wi-001" in downstream
    assert "wi-002" in downstream


def test_subgraph_extraction():
    """Test subgraph extraction for a specific requirement."""
    graph = TraceabilityGraph()
    graph.add_node("chunk-001", "chunk", "Source chunk")
    graph.add_node("req-001", "requirement", "Requirement 1")
    graph.add_node("req-002", "requirement", "Requirement 2")
    graph.add_node("wi-001", "work_item", "Story 1")
    graph.add_edge("chunk-001", "req-001", "derived_from")
    graph.add_edge("req-001", "wi-001", "decomposes_to")
    graph.add_edge("chunk-001", "req-002", "derived_from")

    sub = graph.get_subgraph_for_requirement("req-001")
    sub_json = sub.to_json()

    node_ids = {n["id"] for n in sub_json["nodes"]}
    assert "req-001" in node_ids
    assert "chunk-001" in node_ids
    assert "wi-001" in node_ids
    # req-002 is a sibling, not upstream/downstream of req-001
