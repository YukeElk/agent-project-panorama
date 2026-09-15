import { Bot, CheckCircle2, GitMerge, ListChecks, Pause, Play, RefreshCw, SkipBack, SkipForward, UserRoundCheck } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

const FALLBACK_STEPS = [
  { label: '计划', type: 'ProjectBound', status: 'completed', icon: ListChecks },
  { label: '代理运行', type: 'AgentRunCompleted', status: 'completed', icon: Bot },
  { label: '验证', type: 'VerificationRecorded', status: 'completed', icon: CheckCircle2 },
  { label: '人工接受', type: 'ApprovalRecorded', status: 'completed', icon: UserRoundCheck },
  { label: '提升', type: 'PromotionRecorded', status: 'completed', icon: GitMerge },
  { label: '对齐与调和', type: 'ReconciliationRecorded', status: 'current', icon: RefreshCw },
];

export function DevelopmentTrace({ events, readiness, selectedEvent, onSelect }) {
  const items = events.length > 1 ? events.map((event, index) => ({ ...event, status: index === events.length - 1 ? 'current' : 'completed', icon: FALLBACK_STEPS.find((step) => step.type === event.type)?.icon ?? ListChecks }))
    : readiness?.status === 'passed' ? FALLBACK_STEPS : events.map((event) => ({ ...event, icon: ListChecks }));
  const keys = useMemo(() => items.map((item, index) => item.eventId ?? `${item.type}-${index}`), [items]);
  const selectedIndex = Math.max(0, selectedEvent ? keys.indexOf(selectedEvent) : keys.length - 1);
  const [playing, setPlaying] = useState(false);
  useEffect(() => {
    if (!playing || keys.length < 2) return undefined;
    const timer = window.setInterval(() => {
      const current = selectedEvent ? keys.indexOf(selectedEvent) : keys.length - 1;
      const next = current >= keys.length - 1 ? 0 : current + 1;
      onSelect(keys[next]);
    }, 1200);
    return () => window.clearInterval(timer);
  }, [keys, onSelect, playing, selectedEvent]);
  const moveTo = (index) => onSelect(keys[Math.min(Math.max(index, 0), keys.length - 1)]);
  return (
    <section className="development-trace" aria-labelledby="trace-title">
      <div className="trace-heading">
        <h2 id="trace-title">开发轨迹</h2>
        {readiness ? <span>{readiness.status === 'passed' ? '开发就绪证据已对齐' : '证据需要刷新'}</span> : null}
      </div>
      <div className="trace-line" role="list">
        {items.map((item, index) => {
          const Icon = item.icon;
          const key = item.eventId ?? `${item.type}-${index}`;
          return (
            <button
              className={`trace-step ${item.status === 'current' ? 'is-current' : ''} ${selectedEvent === key ? 'is-selected' : ''}`}
              key={key}
              onClick={() => onSelect(key)}
              type="button"
              role="listitem"
            >
              <span className="trace-icon"><Icon size={18} strokeWidth={1.6} /></span>
              <strong>{item.label}</strong>
              <span className="trace-dot" />
              <small>{item.recordedAt ? new Date(item.recordedAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : item.status === 'current' ? '当前' : '已完成'}</small>
            </button>
          );
        })}
      </div>
      <div className="trace-controls" aria-label="轨迹播放控制">
        <button type="button" aria-label="第一步" onClick={() => moveTo(0)}><SkipBack size={15} /></button>
        <button type="button" aria-label={playing ? '暂停轨迹' : '播放轨迹'} onClick={() => setPlaying((value) => !value)}>{playing ? <Pause size={15} /> : <Play size={15} />}</button>
        <button type="button" aria-label="下一步" onClick={() => moveTo(selectedIndex + 1)}><SkipForward size={15} /></button>
        <input aria-label="轨迹位置" type="range" min="0" max={Math.max(keys.length - 1, 0)} value={selectedIndex} onChange={(event) => moveTo(Number(event.target.value))} />
        <output>{selectedIndex + 1} / {keys.length}</output>
      </div>
    </section>
  );
}
