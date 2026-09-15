import { CircleHelp, Search } from 'lucide-react';

export function TopBar({ projectName, serviceVersion }) {
  return (
    <header className="top-bar">
      <div className="brand">Panorama</div>
      <div className="project-title">{projectName || 'Panorama Control Plane'}</div>
      <div className="top-actions">
        <button type="button" className="icon-button" aria-label="搜索项目"><Search size={19} strokeWidth={1.6} /></button>
        <button type="button" className="icon-button" aria-label={`帮助，版本 ${serviceVersion}`}><CircleHelp size={19} strokeWidth={1.6} /></button>
        <div className="local-avatar" aria-label="本地会话">L</div>
      </div>
    </header>
  );
}
