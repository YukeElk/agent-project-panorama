import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, ArrowUpRight, FileOutput, Layers3, MessageSquare, RefreshCw } from 'lucide-react';
import { applyOperations } from '../../standalone/design-model.mjs';
import { initializeStandaloneCapability, standaloneRequest } from './standalone-api.js';
import { DesignWorkspace } from './components/standalone/DesignWorkspace.jsx';
import { GraphWorkspace } from './components/standalone/GraphWorkspace.jsx';
import { HandoffWorkspace } from './components/standalone/HandoffWorkspace.jsx';
import { shortId } from './components/standalone/Shared.jsx';
import './standalone.css';

const SUCCESS = { refresh: '源码已重新读取。', 'create-session': '设计会话已创建。', 'update-session': '问题与约束已保存。', 'create-candidate': '候选方案已创建。', 'update-candidate': '候选文本已保存。', 'apply-operations': '目标方案已更新，当前源码保持不变。', undo: '已撤销上一次目标变化。', redo: '已恢复目标变化。', 'save-candidate': '方案已保存。', 'save-view': '视角已保存。', 'import-review': '评审原文已保存。', 'decide-review': '评审决策已记录。', 'select-candidate': '方案选择与理由已记录。', 'import-result': '外部结果引用已记录，尚未进行源码核对。', 'export-handoff': '交接说明已生成。', reconcile: '已完成结构核对。', 'bind-target': '对应关系已绑定当前源码快照。' };
const NAVIGATION = [{ id: 'panorama', label: '项目全景', icon: Layers3 }, { id: 'design', label: '设计与评审', icon: MessageSquare }, { id: 'handoff', label: '交接与回读', icon: FileOutput }];

export default function StandaloneApp() {
  const [bootstrap, setBootstrap] = useState(null); const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false); const busyRef = useRef(false); const workspaceRef = useRef(null);
  const [navigation, setNavigation] = useState('panorama'); const [sessionId, setSessionId] = useState(''); const [candidateId, setCandidateId] = useState('');
  const [selectedId, setSelectedId] = useState(null); const [mode, setMode] = useState('current'); const [response, setResponse] = useState(null); const [pending, setPending] = useState(null);
  const updateWorkspace = useCallback((workspace) => { workspaceRef.current = workspace; setBootstrap((current) => ({ ...current, workspace })); }, []);
  useEffect(() => {
    const abort = new AbortController();
    if (!initializeStandaloneCapability()) { setError('请使用启动命令提供的本地链接打开全景。'); return () => abort.abort(); }
    void standaloneRequest('bootstrap', { signal: abort.signal }).then((value) => { workspaceRef.current = value.workspace; setBootstrap(value); }, (failure) => { if (failure.name !== 'AbortError') setError(failure.message); });
    return () => abort.abort();
  }, []);
  const reportFailure = async (failure) => {
    if (failure.status === 409) {
      setError(`工作区或候选版本已变化，已保留表单内容，请核对后重试。${failure.message}`);
      try { const latest = await standaloneRequest('bootstrap'); workspaceRef.current = latest.workspace; setBootstrap(latest); } catch { /* Keep the readable last workspace on reload failure. */ }
    } else setError(failure.message);
  };
  const command = async (type, input) => {
    if (busyRef.current) return null;
    busyRef.current = true; setBusy(true); setError(''); setNotice('');
    try {
      const result = await standaloneRequest('command', { body: { type, input, expectedRevision: workspaceRef.current.revision } });
      updateWorkspace(result.workspace); setNotice(SUCCESS[type] || '工作区已保存。'); return result;
    } catch (failure) { await reportFailure(failure); return null; }
    finally { busyRef.current = false; setBusy(false); }
  };
  const workspace = bootstrap?.workspace;
  const session = workspace?.sessions.find((item) => item.id === sessionId) || workspace?.sessions[0] || null;
  const candidate = workspace?.candidates.find((item) => item.id === candidateId && item.sessionId === session?.id) || workspace?.candidates.find((item) => item.id === session?.selectedCandidateId) || workspace?.candidates.find((item) => item.sessionId === session?.id) || null;
  const selectSession = (id) => { setSessionId(id); setCandidateId(''); setResponse(null); };
  const selectCandidate = (id) => { setCandidateId(id); const value = workspace.candidates.find((item) => item.id === id); if (value) setSessionId(value.sessionId); setResponse(null); };
  const prepare = (operations, note, binding) => {
    if (!candidate) return;
    const proposal = { operations, note, candidateId: binding?.candidateId || candidate.id, candidateVersion: binding?.candidateVersion ?? candidate.version, baseSnapshotId: binding?.baseSnapshotId || candidate.baseSnapshotId };
    try { proposal.preview = applyOperations(candidate.target, operations); }
    catch (failure) { proposal.error = failure.message; }
    setPending(proposal); setNavigation('design');
  };
  const applyPending = async () => {
    if (!pending) return;
    const result = await command('apply-operations', { candidateId: pending.candidateId, candidateVersion: pending.candidateVersion, baseSnapshotId: pending.baseSnapshotId, operations: pending.operations, note: pending.note });
    if (result) { setPending(null); setMode('target'); }
  };
  const converse = async (message) => {
    if (!session || busyRef.current) return null;
    busyRef.current = true; setBusy(true); setError(''); setNotice('');
    try {
      const result = await standaloneRequest('conversation', { body: { sessionId: session.id, candidateId: candidate?.id, selectedId, message, expectedRevision: workspaceRef.current.revision } });
      updateWorkspace(result.workspace); setResponse(result); return result;
    } catch (failure) { await reportFailure(failure); return null; }
    finally { busyRef.current = false; setBusy(false); }
  };
  if (!bootstrap) return <main className={error ? 'fatal-state' : 'loading-state'}>{error ? <><AlertTriangle size={28} /><h1>无法打开项目全景</h1><p>{error}</p><button type="button" className="secondary-button" onClick={() => window.location.reload()}>重新连接</button></> : <><span className="loading-mark" /><p>正在读取源码模型与设计工作区…</p></>}</main>;
  return <div className="st-shell">
    <header className="st-topbar"><div className="st-brand"><Layers3 size={23} /><strong>Panorama</strong><span>项目全景</span></div><div className="st-project-title"><strong>{workspace.current.project.name}</strong><span title={workspace.current.snapshot.id}>源码快照 {shortId(workspace.current.snapshot.id)}</span></div><button type="button" className="secondary-button" disabled={busy} onClick={() => command('refresh', {})}><RefreshCw size={15} className={busy ? 'st-busy' : ''} />重新解析</button></header>
    <nav className="st-navigation" aria-label="主导航">{NAVIGATION.map(({ id, label, icon: Icon }) => <button type="button" key={id} aria-current={navigation === id ? 'page' : undefined} onClick={() => setNavigation(id)}><Icon size={20} /><span>{label}</span></button>)}<p>项目理解<br />目标设计<br />外部交接</p></nav>
    <div className="st-content">
      <div className="st-global-context"><label>设计上下文<select aria-label="当前候选方案" value={candidate?.id || ''} onChange={(event) => selectCandidate(event.target.value)}><option value="" disabled>{workspace.candidates.length ? '选择候选方案' : '尚无候选方案'}</option>{workspace.candidates.map((item) => <option key={item.id} value={item.id}>{workspace.sessions.find((value) => value.id === item.sessionId)?.title} / {item.title}</option>)}</select></label><button type="button" className="st-text-button" onClick={() => setNavigation(navigation === 'design' ? 'panorama' : 'design')}>{navigation === 'design' ? '在图上查看' : '进入设计'}<ArrowUpRight size={14} /></button><span className="st-revision">工作区修订 {workspace.revision}</span></div>
      {error ? <div className="st-error-banner" role="alert"><AlertTriangle size={17} /><span>{error}</span><button type="button" onClick={() => setError('')}>关闭</button></div> : null}
      {notice ? <div className="st-notice" role="status">{notice}<button type="button" onClick={() => setNotice('')}>关闭</button></div> : null}
      {busy ? <div className="st-progress" role="status">正在处理，请稍候…</div> : null}
      <div hidden={navigation !== 'panorama'}><GraphWorkspace workspace={workspace} candidate={candidate} selectedId={selectedId} onSelect={setSelectedId} command={command} busy={busy} mode={mode} onMode={setMode} /></div>
      {navigation === 'design' ? <DesignWorkspace workspace={workspace} session={session} candidate={candidate} selectSession={selectSession} selectCandidate={selectCandidate} command={command} busy={busy} selectedId={selectedId} prepare={prepare} converse={converse} model={bootstrap.model} response={response} clearResponse={() => setResponse(null)} pending={pending} applyPending={applyPending} discardPending={() => setPending(null)} /> : null}
      <div hidden={navigation !== 'handoff'}><HandoffWorkspace workspace={workspace} candidate={candidate} command={command} busy={busy} /></div>
    </div>
  </div>;
}
