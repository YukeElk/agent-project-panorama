import { Boxes, Clock3, GitFork, Maximize2, Minus, Network, Plus, Route } from 'lucide-react';
import { useState } from 'react';

import { DevelopmentTrace } from './DevelopmentTrace.jsx';
import { EvidenceInspector } from './EvidenceInspector.jsx';
import { PanoramaGraph } from './PanoramaGraph.jsx';

const VIEWPOINTS = [
  { id: 'architecture', label: '架构', icon: Network },
  { id: 'dependency', label: '依赖', icon: Boxes },
  { id: 'dataflow', label: '数据流', icon: GitFork },
  { id: 'sequence', label: '时序', icon: Clock3 },
  { id: 'business', label: '业务流', icon: Route },
];

export function PanoramaView({ graph, projection, capabilities, onViewpoint }) {
  const [selectedId, setSelectedId] = useState('control-kernel');
  const [zoom, setZoom] = useState(100);
  const [selectedEvent, setSelectedEvent] = useState(null);
  const selectedNode = graph.nodes.find((node) => node.id === selectedId) ?? graph.nodes[0];
  return (
    <div className="panorama-layout">
      <main className="panorama-main">
        <div className="workspace-heading">
          <h1>项目全景</h1>
          <span className="branch-label">{graph.project.head === 'not-git' ? '未检测到 Git' : `${graph.project.branch} · ${graph.project.dirty ? '工作区有变更' : 'Git 当前'}`}</span>
        </div>
        <div className="graph-toolbar">
          <div className="viewpoint-switch" aria-label="视图类型">
            {VIEWPOINTS.map((viewpoint) => {
              const Icon = viewpoint.icon;
              return (
                <button type="button" key={viewpoint.id} className={graph.viewpoint === viewpoint.id ? 'is-selected' : ''} onClick={() => onViewpoint(viewpoint.id)}>
                  <Icon size={17} strokeWidth={1.6} /><span>{viewpoint.label}</span>
                </button>
              );
            })}
          </div>
          <div className="zoom-controls">
            <button type="button" aria-label="适合画布" onClick={() => setZoom(100)}><Maximize2 size={17} /></button>
            <button type="button" aria-label="缩小" onClick={() => setZoom((value) => Math.max(80, value - 10))}><Minus size={17} /></button>
            <output>{zoom}%</output>
            <button type="button" aria-label="放大" onClick={() => setZoom((value) => Math.min(140, value + 10))}><Plus size={17} /></button>
          </div>
        </div>
        <PanoramaGraph graph={graph} selectedId={selectedId} onSelect={setSelectedId} zoom={zoom} />
        <DevelopmentTrace events={projection.timeline} readiness={graph.readiness} selectedEvent={selectedEvent} onSelect={setSelectedEvent} />
      </main>
      <EvidenceInspector node={selectedNode} graph={graph} capabilities={capabilities} />
    </div>
  );
}
