import { ExternalLink, Layers3 } from 'lucide-react';

function StatusTag({ children, tone = 'neutral' }) {
  return <span className={`status-tag tone-${tone}`}>{children}</span>;
}

export function EvidenceInspector({ node, graph, capabilities }) {
  const incoming = graph.edges.filter((edge) => edge.from === node.id || edge.to === node.id);
  const authority = node.id === 'git-verifier' ? 'Git / Independent Verifier'
    : node.id === 'workbench' ? 'Read Projection'
      : node.id.includes('runtime') || node.id.includes('adapter') || node.id.includes('cordis') ? 'Runtime Observation'
        : 'Domain Log / Source';
  return (
    <aside className="evidence-inspector" aria-labelledby="inspector-title">
      <div className="inspector-heading">
        <div>
          <h2 id="inspector-title">证据检查器</h2>
          <span>选中节点</span>
        </div>
        <Layers3 size={21} strokeWidth={1.6} aria-hidden="true" />
      </div>
      <div className="selected-node">
        <strong>{node.label}</strong>
        <span>{node.subtitle}</span>
      </div>
      <dl className="inspector-fields">
        <div><dt>状态</dt><dd><StatusTag tone={node.status === 'current' ? 'verified' : 'attention'}>{node.status === 'current' ? '当前' : '待实现'}</StatusTag></dd></div>
        <div><dt>源版本</dt><dd className="mono">{graph.project.head.slice(0, 9)}</dd></div>
        <div><dt>新鲜度</dt><dd>{graph.project.head === 'not-git' ? '未检测到 Git' : graph.project.dirty ? '存在工作区变更' : '与 Git 一致'}</dd></div>
        <div><dt>权威</dt><dd>{authority}</dd></div>
        <div><dt>下一步</dt><dd className="next-action">检查关联证据</dd></div>
      </dl>
      <section className="evidence-list" aria-labelledby="evidence-list-title">
        <div className="section-heading">
          <h3 id="evidence-list-title">证据项 ({incoming.length + 1})</h3>
        </div>
        <div className="evidence-row">
          <span className="mono">{node.evidence.ref}</span>
          <span>{node.evidence.fileCount} 个源码文件</span>
          <StatusTag tone={node.status === 'current' ? 'verified' : 'attention'}>{node.status === 'current' ? '已绑定' : '待建立'}</StatusTag>
          <ExternalLink size={15} aria-hidden="true" />
        </div>
        {incoming.map((edge) => (
          <div className="evidence-row" key={edge.id}>
            <span className="mono">#{edge.id.length.toString(16).padStart(6, '0')}</span>
            <span>{edge.label}</span>
            <StatusTag tone={edge.active ? 'verified' : 'neutral'}>{edge.active ? '当前视图' : '可查询'}</StatusTag>
            <ExternalLink size={15} aria-hidden="true" />
          </div>
        ))}
      </section>
      <section className="provider-section">
        <h3>宿主能力</h3>
        {capabilities.map((provider) => (
          <div className="provider-row" key={provider.adapterId}>
            <span>{provider.adapterId}</span>
            <StatusTag tone={provider.live ? 'verified' : 'attention'}>{provider.live ? 'live' : 'source-bound'}</StatusTag>
          </div>
        ))}
      </section>
    </aside>
  );
}
