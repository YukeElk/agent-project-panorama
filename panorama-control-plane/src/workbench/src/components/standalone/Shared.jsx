import { useState } from 'react';
import { diffModels } from '../../../../standalone/design-model.mjs';
import { copyText } from '../../browser-utils.js';

export const KIND_LABELS = { module: '模块', file: '文件', symbol: '符号', route: '入口', service: '服务', resource: '资源', external: '外部依赖' };
export const STATUS_LABELS = { reflected: '结构已体现', partial: '部分体现', not_reflected: '尚未体现', insufficient_evidence: '证据不足', pending: '待处理', accepted: '已采纳', partially_accepted: '部分采纳', rejected: '已驳回' };
export const VIEW_LABELS = { modules: '模块', dependencies: '依赖', deployment: '部署' };
export const operationTitle = (operation) => ({ 'add-node': '新增节点', 'update-node': '调整节点', 'remove-node': '移除节点', 'add-edge': '新增关系', 'update-edge': '调整关系', 'remove-edge': '移除关系' }[operation.type] || operation.type);
export const shortId = (id) => id ? id.slice(0, 12) : '—';
export const lines = (value) => String(value || '').split(/\n|，|,/).map((item) => item.trim()).filter(Boolean);

export function Field({ label, children, hint }) {
  return <label className="st-field"><span>{label}</span>{children}{hint ? <small>{hint}</small> : null}</label>;
}

export function Empty({ title, children }) {
  return <div className="st-empty"><h3>{title}</h3><p>{children}</p></div>;
}

export function Tag({ children, tone = '' }) { return <span className={`st-tag ${tone}`}>{children}</span>; }

export function CopyButton({ text, label = '复制说明' }) {
  const [status, setStatus] = useState('');
  return <span className="st-copy"><button type="button" className="secondary-button" disabled={!text} onClick={async () => {
    try { await copyText(text); setStatus('已复制'); }
    catch { setStatus('复制不可用，请选择下方文本复制'); }
  }}>{status === '已复制' ? status : label}</button>{status && status !== '已复制' ? <small role="status">{status}</small> : null}</span>;
}

export function downloadText(text, filename, type = 'text/plain;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement('a');
  link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function Operations({ operations = [], model, selected, onSelection }) {
  const names = new Map((model?.nodes || []).map((node) => [node.id, node.label]));
  return <ol className="st-operations">{operations.map((operation, index) => <li key={index}>
    <div>{onSelection ? <input type="checkbox" aria-label={`选择建议 ${index + 1}`} checked={selected.includes(index)} onChange={() => onSelection(selected.includes(index) ? selected.filter((item) => item !== index) : [...selected, index])} /> : null}
      <strong>{operationTitle(operation)}</strong><span>{operation.node?.label || operation.edge?.label || names.get(operation.id) || operation.id}</span></div>
    <pre>{JSON.stringify(operation.changes || operation.node?.attributes || operation.edge || {}, null, 2)}</pre>
  </li>)}</ol>;
}

export function DiffSummary({ base, target }) {
  if (!base || !target) return null;
  const diff = diffModels(base, target);
  return <div className="st-diff-summary">{[['节点', diff.nodes], ['关系', diff.edges]].map(([name, group]) => <span key={name}>{name} <b className="st-added">+{group.added.length}</b> <b className="st-removed">−{group.removed.length}</b> <b className="st-changed">~{group.changed.length}</b></span>)}</div>;
}
