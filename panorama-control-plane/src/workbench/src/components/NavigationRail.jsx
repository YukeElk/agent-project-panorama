import {
  CheckSquare2,
  GitPullRequestDraft,
  LayoutDashboard,
  ListTodo,
  Network,
} from 'lucide-react';

const ITEMS = [
  { id: 'cockpit', label: '驾驶舱', icon: LayoutDashboard },
  { id: 'panorama', label: '项目全景', icon: Network },
  { id: 'missions', label: '任务', icon: ListTodo },
  { id: 'sessions', label: '变更会话', icon: GitPullRequestDraft },
  { id: 'verification', label: '验证与接受', icon: CheckSquare2 },
];

export function NavigationRail({ current, onSelect }) {
  return (
    <nav className="navigation-rail" aria-label="主导航">
      <div className="nav-items">
        {ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <button
              className={`nav-item ${current === item.id ? 'is-selected' : ''}`}
              key={item.id}
              onClick={() => onSelect(item.id)}
              type="button"
              aria-label={item.label}
              aria-current={current === item.id ? 'page' : undefined}
            >
              <Icon aria-hidden="true" size={20} strokeWidth={1.6} />
              <span>{item.label}</span>
            </button>
          );
        })}
      </div>
      <div className="nav-foot">本地控制平面</div>
    </nav>
  );
}
