import { useEffect, useRef } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import * as d3 from 'd3';

interface GraphNode {
  id: string;
  type: string;
  label: string;
  metadata?: Record<string, unknown>;
}

interface GraphEdge {
  source: string;
  target: string;
  link_type: string;
  confidence: number;
}

interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';

const NODE_COLORS: Record<string, string> = {
  source_document: '#64748b',
  chunk: '#475569',
  requirement: '#00d4aa',
  work_item: '#f59e0b',
  ado_work_item: '#0078d4',
};

const NODE_RADIUS: Record<string, number> = {
  source_document: 12,
  chunk: 6,
  requirement: 10,
  work_item: 10,
  ado_work_item: 14,
};

export default function TraceabilityGraph() {
  const { sessionId = '' } = useParams<{ sessionId: string }>();
  const svgRef = useRef<SVGSVGElement>(null);

  const { data: graphData } = useQuery<GraphData>({
    queryKey: ['traceability', sessionId],
    queryFn: async () => {
      const res = await fetch(`${API_URL}/sessions/${sessionId}/graph`);
      if (!res.ok) {
        throw new Error(`Failed to load graph: HTTP ${res.status}`);
      }
      const json = await res.json();
      // Defensive: backend may legitimately return an empty graph for a
      // brand-new session. Normalise to a stable shape so downstream code
      // can iterate without optional-chaining everywhere.
      return {
        nodes: Array.isArray(json?.nodes) ? json.nodes : [],
        edges: Array.isArray(json?.edges) ? json.edges : [],
      };
    },
    enabled: Boolean(sessionId),
  });

  useEffect(() => {
    if (!graphData || !svgRef.current) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const width = svgRef.current.clientWidth;
    const height = svgRef.current.clientHeight;

    // Create zoom behavior
    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.2, 4])
      .on('zoom', (event) => {
        container.attr('transform', event.transform);
      });

    svg.call(zoom);
    const container = svg.append('g');

    // Build simulation data
    const nodes = (graphData.nodes ?? []).map((n) => ({ ...n }));
    const links = (graphData.edges ?? []).map((e) => ({
      source: e.source,
      target: e.target,
      link_type: e.link_type,
      confidence: e.confidence,
    }));

    // Force simulation
    const simulation = d3
      .forceSimulation(nodes as any)
      .force(
        'link',
        d3
          .forceLink(links as any)
          .id((d: any) => d.id)
          .distance(80),
      )
      .force('charge', d3.forceManyBody().strength(-200))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(20));

    // Draw edges
    const link = container
      .append('g')
      .selectAll('line')
      .data(links)
      .enter()
      .append('line')
      .attr('stroke', '#334155')
      .attr('stroke-width', (d: any) => d.confidence * 2)
      .attr('stroke-opacity', 0.6);

    // Draw edge labels
    const linkLabel = container
      .append('g')
      .selectAll('text')
      .data(links)
      .enter()
      .append('text')
      .attr('font-size', '8px')
      .attr('fill', '#64748b')
      .attr('font-family', 'Geist Mono, monospace')
      .text((d: any) => d.link_type);

    // Draw nodes
    const node = container
      .append('g')
      .selectAll('circle')
      .data(nodes)
      .enter()
      .append('circle')
      .attr('r', (d: any) => NODE_RADIUS[d.type] || 8)
      .attr('fill', (d: any) => NODE_COLORS[d.type] || '#64748b')
      .attr('stroke', '#1e293b')
      .attr('stroke-width', 2)
      .call(
        d3
          .drag<any, any>()
          .on('start', (event, d: any) => {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
          })
          .on('drag', (event, d: any) => {
            d.fx = event.x;
            d.fy = event.y;
          })
          .on('end', (event, d: any) => {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
          }),
      );

    // Node labels
    const label = container
      .append('g')
      .selectAll('text')
      .data(nodes)
      .enter()
      .append('text')
      .attr('font-size', '10px')
      .attr('fill', '#94a3b8')
      .attr('font-family', 'Geist Mono, monospace')
      .attr('dx', 15)
      .attr('dy', 4)
      .text((d: any) => d.label.slice(0, 30));

    // Tooltip
    node.append('title').text((d: any) => `${d.type}: ${d.label}`);

    // Tick handler
    simulation.on('tick', () => {
      link
        .attr('x1', (d: any) => d.source.x)
        .attr('y1', (d: any) => d.source.y)
        .attr('x2', (d: any) => d.target.x)
        .attr('y2', (d: any) => d.target.y);

      linkLabel
        .attr('x', (d: any) => (d.source.x + d.target.x) / 2)
        .attr('y', (d: any) => (d.source.y + d.target.y) / 2);

      node.attr('cx', (d: any) => d.x).attr('cy', (d: any) => d.y);

      label.attr('x', (d: any) => d.x).attr('y', (d: any) => d.y);
    });

    return () => {
      simulation.stop();
    };
  }, [graphData]);

  return (
    <div className="p-6 md:p-10">
      <div className="mx-auto max-w-7xl">
        {/* Header */}
        <div className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-2xl font-bold tracking-tight">Traceability Graph</h1>
          <div className="flex items-center gap-4">
            {/* Legend */}
            {Object.entries(NODE_COLORS).map(([type, color]) => (
              <div key={type} className="flex items-center gap-1.5">
                <div className="h-3 w-3 rounded-full" style={{ backgroundColor: color }} />
                <span className="text-xs capitalize text-slate-400">{type.replace('_', ' ')}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Graph Canvas */}
        <div className="overflow-hidden rounded-xl border border-slate-800 bg-slate-900">
          <svg ref={svgRef} className="w-full" style={{ height: '70vh' }} />
        </div>

        {/* Stats */}
        {graphData && (
          <div className="mt-4 flex gap-4">
            <div className="rounded-lg border border-slate-800 bg-slate-900 px-4 py-2">
              <span className="text-xs text-slate-400">Nodes</span>
              <span className="ml-2 font-['Geist_Mono'] text-teal-400">
                {graphData.nodes.length}
              </span>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-900 px-4 py-2">
              <span className="text-xs text-slate-400">Edges</span>
              <span className="ml-2 font-['Geist_Mono'] text-teal-400">
                {graphData.edges.length}
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
