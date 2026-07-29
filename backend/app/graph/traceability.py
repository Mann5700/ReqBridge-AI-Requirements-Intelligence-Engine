"""NetworkX-based traceability graph supporting impact analysis and JSON export."""

from typing import Optional

import networkx as nx


class TraceabilityGraph:
    """NetworkX-based knowledge graph for requirements traceability.

    Node types: source_document, chunk, requirement, work_item, ado_work_item
    Edge types: derived_from, decomposes_to, tested_by, pushed_as, parent_of

    Supports:
    - Building graph from database traceability links
    - Impact analysis (upstream/downstream traversal)
    - JSON serialization for API/MCP responses
    - Subgraph extraction for specific requirements
    """

    def __init__(self) -> None:
        self.graph = nx.DiGraph()

    def add_node(
        self,
        node_id: str,
        node_type: str,
        label: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """Add a node to the traceability graph."""
        self.graph.add_node(
            node_id,
            node_type=node_type,
            label=label,
            metadata=metadata or {},
        )

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        link_type: str,
        confidence: float = 1.0,
    ) -> None:
        """Add a directed edge between two nodes."""
        self.graph.add_edge(
            source_id,
            target_id,
            link_type=link_type,
            confidence=confidence,
        )

    def build_from_links(self, links: list[dict], nodes_metadata: dict[str, dict] | None = None) -> None:
        """Reconstruct graph from a list of traceability link dicts.

        Each link dict has: source_node_type, source_node_id,
        target_node_type, target_node_id, link_type, confidence
        """
        nodes_metadata = nodes_metadata or {}

        for link in links:
            src_id = link["source_node_id"]
            tgt_id = link["target_node_id"]
            src_type = link["source_node_type"]
            tgt_type = link["target_node_type"]

            # Add nodes if not present
            if src_id not in self.graph:
                meta = nodes_metadata.get(src_id, {})
                self.add_node(
                    src_id,
                    node_type=src_type,
                    label=meta.get("label", src_id),
                    metadata=meta,
                )
            if tgt_id not in self.graph:
                meta = nodes_metadata.get(tgt_id, {})
                self.add_node(
                    tgt_id,
                    node_type=tgt_type,
                    label=meta.get("label", tgt_id),
                    metadata=meta,
                )

            self.add_edge(
                src_id,
                tgt_id,
                link_type=link["link_type"],
                confidence=link.get("confidence", 1.0),
            )

    def get_impact_downstream(self, node_id: str) -> list[str]:
        """Get all nodes downstream of a given node (impact analysis).

        Answers: "If this node changes, what is affected?"
        """
        if node_id not in self.graph:
            return []
        return list(nx.descendants(self.graph, node_id))

    def get_impact_upstream(self, node_id: str) -> list[str]:
        """Get all nodes upstream of a given node (provenance analysis).

        Answers: "Where did this node come from?"
        """
        if node_id not in self.graph:
            return []
        return list(nx.ancestors(self.graph, node_id))

    def get_subgraph_for_requirement(self, requirement_id: str) -> "TraceabilityGraph":
        """Extract the subgraph rooted at a specific requirement.

        Returns all upstream sources and downstream work items/ADO items.
        """
        related_nodes = set()
        related_nodes.add(requirement_id)
        related_nodes.update(self.get_impact_upstream(requirement_id))
        related_nodes.update(self.get_impact_downstream(requirement_id))

        subgraph = TraceabilityGraph()
        subgraph.graph = self.graph.subgraph(related_nodes).copy()
        return subgraph

    def to_json(self) -> dict:
        """Serialize graph to JSON format suitable for API responses and D3.js visualization.

        Returns:
            Dict with 'nodes' and 'edges' arrays matching the TraceabilityGraphResponse schema.
        """
        nodes = []
        for node_id, data in self.graph.nodes(data=True):
            nodes.append({
                "id": node_id,
                "type": data.get("node_type", "unknown"),
                "label": data.get("label", node_id),
                "metadata": data.get("metadata", {}),
            })

        edges = []
        for source, target, data in self.graph.edges(data=True):
            edges.append({
                "source": source,
                "target": target,
                "link_type": data.get("link_type", "related_to"),
                "confidence": data.get("confidence", 1.0),
            })

        return {"nodes": nodes, "edges": edges}

    def get_stats(self) -> dict:
        """Return graph statistics for dashboard display."""
        node_types: dict[str, int] = {}
        for _, data in self.graph.nodes(data=True):
            nt = data.get("node_type", "unknown")
            node_types[nt] = node_types.get(nt, 0) + 1

        return {
            "total_nodes": self.graph.number_of_nodes(),
            "total_edges": self.graph.number_of_edges(),
            "node_types": node_types,
            "is_dag": nx.is_directed_acyclic_graph(self.graph),
        }
