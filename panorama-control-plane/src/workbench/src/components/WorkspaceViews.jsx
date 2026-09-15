import { ArrowRight, Bot, CheckCircle2, GitBranch, Plus, ShieldCheck } from 'lucide-react';
import { useState } from 'react';

function EmptyState({ title, description }) {
  return <div className="empty-state"><h2>{title}</h2><p>{description}</p></div>;
}

export function CockpitView({ projection, graph }) {
  const active = projection.sessions.filter((session) => !['reconciled', 'failed'].includes(session.status));
  return (
    <main className="workspace-page">
      <div className="workspace-heading"><h1>驾驶舱</h1><span>Revision {projection.revision}</span></div>
      <section className="cockpit-summary">
        <div><strong>{projection.missions.length}</strong><span>任务</span></div>
        <div><strong>{active.length}</strong><span>活跃会话</span></div>
        <div><strong>{graph.nodes.filter((node) => node.status === 'current').length}</strong><span>已绑定模块</span></div>
        <div><strong>{graph.readiness?.status === 'passed' ? '当前' : '待验证'}</strong><span>Evidence freshness</span></div>
      </section>
      <section className="open-section">
        <h2>当前项目</h2>
        <dl className="project-facts">
          <div><dt>Git 分支</dt><dd>{graph.project.branch}</dd></div>
          <div><dt>源版本</dt><dd className="mono">{graph.project.head.slice(0, 12)}</dd></div>
          <div><dt>工作区</dt><dd>{graph.project.dirty ? '存在未提交变更' : '干净'}</dd></div>
          <div><dt>开发就绪</dt><dd>{graph.readiness?.status ?? '无证据'}</dd></div>
        </dl>
      </section>
    </main>
  );
}

export function MissionsView({ projection, onCommand, busy }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [goal, setGoal] = useState('');
  const submit = async (event) => {
    event.preventDefault();
    const succeeded = await onCommand('create-mission', { title, goal, successCriteria: 1 });
    if (succeeded) { setTitle(''); setGoal(''); setOpen(false); }
  };
  return (
    <main className="workspace-page">
      <div className="workspace-heading"><h1>任务</h1><button className="primary-button" type="button" onClick={() => setOpen((value) => !value)}><Plus size={17} />创建任务</button></div>
      {open ? (
        <form className="command-form" onSubmit={submit}>
          <label>任务标题<input value={title} onChange={(event) => setTitle(event.target.value)} minLength="3" maxLength="80" required /></label>
          <label>目标<textarea value={goal} onChange={(event) => setGoal(event.target.value)} minLength="8" maxLength="400" required /></label>
          <div><button type="button" className="secondary-button" onClick={() => setOpen(false)}>取消</button><button disabled={busy} className="primary-button" type="submit">保存任务</button></div>
        </form>
      ) : null}
      {projection.missions.length === 0 ? <EmptyState title="还没有任务" description="创建任务后，平台会用 WorkPackage 和变更会话约束 Agent 执行。" /> : (
        <div className="entity-list">{projection.missions.map((mission) => <article key={mission.missionId}><div><h2>{mission.title}</h2><p>{mission.goal}</p></div><span className="status-tag tone-verified">{mission.status}</span></article>)}</div>
      )}
    </main>
  );
}

export function SessionsView({ projection, graph, onCommand, busy }) {
  const [missionId, setMissionId] = useState(projection.missions[0]?.missionId ?? '');
  const [adapterId, setAdapterId] = useState('replay');
  const gitReady = /^[a-f0-9]{40,64}$/.test(graph.project.head) && !graph.project.dirty;
  const createSession = () => onCommand('create-change-session', { missionId, adapterId, baseRevision: graph.project.head });
  const sessionAction = (session) => {
    if (session.status === 'ready') return session.adapterId === 'codex'
      ? { label: '运行 Codex', icon: Bot, command: 'run-codex' }
      : { label: '运行 Replay', icon: Bot, command: 'run-replay' };
    if (session.status === 'change_ready' && session.candidateRevision) return { label: '独立验证', icon: CheckCircle2, command: 'verify-change' };
    if (session.status === 'verified') return { label: '人工接受', icon: ShieldCheck, command: 'approve-change' };
    if (session.status === 'accepted') return { label: '提升并对账', icon: GitBranch, command: 'promote-change' };
    return null;
  };
  return (
    <main className="workspace-page">
      <div className="workspace-heading"><h1>变更会话</h1></div>
      {projection.missions.length > 0 ? (
        <div className="session-create-bar">
          <label>从任务创建<select value={missionId} onChange={(event) => setMissionId(event.target.value)}>{projection.missions.map((mission) => <option key={mission.missionId} value={mission.missionId}>{mission.title}</option>)}</select></label>
          <label>执行适配器<select value={adapterId} onChange={(event) => setAdapterId(event.target.value)}><option value="replay">Replay</option><option value="codex" disabled={!gitReady}>Codex（需要干净 Git 项目）</option></select></label>
          <button disabled={busy || !missionId} className="primary-button" type="button" onClick={createSession}><Plus size={17} />创建 {adapterId === 'codex' ? 'Codex' : 'Replay'} 会话</button>
        </div>
      ) : <EmptyState title="需要先创建任务" description="变更会话必须绑定一个明确任务、Git 基线和能力决策。" />}
      <div className="entity-list session-list">{projection.sessions.map((session) => {
        const action = sessionAction(session);
        const ActionIcon = action?.icon;
        return (
          <article key={session.sessionId}>
            <div className="session-icon"><GitBranch size={20} /></div>
            <div><h2>{session.title}</h2><p className="mono">{session.sessionId} · {session.baseRevision.slice(0, 10)} · {session.adapterId}</p></div>
            <span className={`status-tag ${['change_ready', 'verified', 'accepted', 'reconciled'].includes(session.status) ? 'tone-verified' : 'tone-neutral'}`}>{session.status}</span>
            {action ? <button disabled={busy} type="button" className={action.command === 'promote-change' ? 'primary-button' : 'secondary-button'} onClick={() => onCommand(action.command, { sessionId: session.sessionId })}><ActionIcon size={16} />{action.label}</button> : <ArrowRight size={17} />}
          </article>
        );
      })}</div>
    </main>
  );
}

export function VerificationView({ projection }) {
  const evidence = projection.sessions.flatMap((session) => session.evidence.map((item) => ({ ...item, sessionId: session.sessionId })));
  return (
    <main className="workspace-page">
      <div className="workspace-heading"><h1>验证与接受</h1></div>
      <div className="verification-rule"><ShieldCheck size={21} /><p>只有独立签名 Receipt、当前撤销状态和精确 Git Hash 能推动 verified / accepted / integrated。</p></div>
      {evidence.length === 0 ? <EmptyState title="没有权威验证记录" description="Agent 完成最多推进到 change_ready；验证和人工接受不会自动发生。" /> : (
        <div className="entity-list">{evidence.map((item) => <article key={`${item.sessionId}-${item.receiptHash}`}><CheckCircle2 size={20} /><div><h2>{item.kind}</h2><p className="mono">{item.receiptHash}</p></div><span className="status-tag tone-verified">{item.status}</span></article>)}</div>
      )}
    </main>
  );
}
