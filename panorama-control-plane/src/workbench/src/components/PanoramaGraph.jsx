import { useMemo } from 'react';

function edgeGeometry(from, to) {
  const sameLayer = Math.abs(from.y - to.y) < 12;
  if (sameLayer) {
    const leftToRight = to.x > from.x;
    const startX = leftToRight ? from.x + from.width : from.x;
    const endX = leftToRight ? to.x : to.x + to.width;
    const y = from.y + from.height / 2;
    return { path: `M ${startX} ${y} H ${endX}`, labelX: (startX + endX) / 2, labelY: y - 8 };
  }
  const startX = from.x + from.width / 2;
  const endX = to.x + to.width / 2;
  if (to.y > from.y) {
    const startY = from.y + from.height;
    const endY = to.y;
    const midY = (startY + endY) / 2;
    return { path: `M ${startX} ${startY} V ${midY} H ${endX} V ${endY}`, labelX: (startX + endX) / 2, labelY: midY - 6 };
  }
  const startY = from.y;
  const endY = to.y + to.height / 2;
  const gutterX = 882;
  return { path: `M ${startX} ${startY} V ${startY - 20} H ${gutterX} V ${endY} H ${to.x + to.width}`, labelX: gutterX - 42, labelY: endY - 7 };
}

export function PanoramaGraph({ graph, selectedId, onSelect, zoom }) {
  const nodeMap = useMemo(() => new Map(graph.nodes.map((node) => [node.id, node])), [graph.nodes]);
  return (
    <div className="graph-scroll" aria-label="项目架构图">
      <svg className="panorama-graph" viewBox="0 0 900 540" style={{ width: `${zoom}%` }} role="img" aria-labelledby="graph-title">
        <title id="graph-title">项目模块与证据关系</title>
        {[166, 291, 416].map((y) => <line className="layer-rule" key={y} x1="32" x2="868" y1={y} y2={y} />)}
        {graph.edges.map((edge) => {
          const from = nodeMap.get(edge.from);
          const to = nodeMap.get(edge.to);
          const geometry = edgeGeometry(from, to);
          const connected = edge.active && (edge.from === selectedId || edge.to === selectedId);
          return (
            <g key={edge.id} className={`graph-edge ${edge.active ? 'is-active' : ''} ${connected ? 'is-connected' : ''}`}>
              <path d={geometry.path} />
              {edge.active ? <text x={geometry.labelX} y={geometry.labelY}>{edge.label}</text> : null}
            </g>
          );
        })}
        {graph.nodes.map((node) => {
          const selected = node.id === selectedId;
          return (
            <g
              className={`graph-node ${selected ? 'is-selected' : ''} ${node.status === 'planned' ? 'is-planned' : ''}`}
              key={node.id}
              onClick={() => onSelect(node.id)}
              onKeyDown={(event) => (event.key === 'Enter' || event.key === ' ') && onSelect(node.id)}
              role="button"
              tabIndex="0"
              aria-label={`${node.label}，${node.subtitle}`}
            >
              <rect x={node.x} y={node.y} width={node.width} height={node.height} rx="7" />
              <path className="node-mark" d={`M ${node.x + 18} ${node.y + 18} l 7 -4 l 7 4 v 9 l -7 4 l -7 -4 z`} />
              <text className="node-label" x={node.x + 44} y={node.y + 25}>{node.label}</text>
              <text className="node-subtitle" x={node.x + 44} y={node.y + 44}>{node.subtitle}</text>
              <g className="evidence-marker">
                <rect x={node.x + node.width - 58} y={node.y + node.height - 9} width="52" height="18" rx="3" />
                <text x={node.x + node.width - 32} y={node.y + node.height + 3} textAnchor="middle">{node.evidence.ref.slice(-8)}</text>
              </g>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
