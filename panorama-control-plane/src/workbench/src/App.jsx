import { AlertTriangle } from 'lucide-react';
import { useEffect, useState, useTransition } from 'react';

import { apiRequest, initializeCapability, submitCommand } from './api.js';
import { NavigationRail } from './components/NavigationRail.jsx';
import { PanoramaView } from './components/PanoramaView.jsx';
import { TopBar } from './components/TopBar.jsx';
import { CockpitView, MissionsView, SessionsView, VerificationView } from './components/WorkspaceViews.jsx';

export default function App() {
  const [navigation, setNavigation] = useState('panorama');
  const [bootstrap, setBootstrap] = useState(null);
  const [error, setError] = useState(null);
  const [commandBusy, setCommandBusy] = useState(false);
  const [isPending, startTransition] = useTransition();

  useEffect(() => {
    if (!initializeCapability()) {
      setError('CAPABILITY_REQUIRED');
      return;
    }
    void apiRequest('/api/v1/bootstrap').then(setBootstrap, (requestError) => setError(requestError.message));
  }, []);

  const changeViewpoint = (viewpoint) => {
    startTransition(() => {
      void apiRequest(`/api/v1/views?viewpoint=${encodeURIComponent(viewpoint)}`).then((graph) => setBootstrap((current) => ({ ...current, graph })), (requestError) => setError(requestError.message));
    });
  };

  const command = async (type, input) => {
    setError(null);
    setCommandBusy(true);
    try {
      const projection = await submitCommand(type, input, bootstrap.projection.revision);
      setBootstrap((current) => ({ ...current, projection }));
      return true;
    } catch (commandError) {
      setError(commandError.message);
      return false;
    } finally {
      setCommandBusy(false);
    }
  };

  if (error && !bootstrap) {
    return <main className="fatal-state"><AlertTriangle size={28} /><h1>无法打开本地控制平面</h1><p>{error === 'CAPABILITY_REQUIRED' ? '请使用启动命令输出的带 capability 链接重新打开页面。' : error}</p></main>;
  }
  if (!bootstrap) return <main className="loading-state"><span className="loading-mark" />正在读取本地项目事实…</main>;

  const common = { projection: bootstrap.projection, graph: bootstrap.graph, onCommand: command, busy: commandBusy || isPending };
  let content;
  if (navigation === 'cockpit') content = <CockpitView projection={bootstrap.projection} graph={bootstrap.graph} />;
  else if (navigation === 'missions') content = <MissionsView {...common} />;
  else if (navigation === 'sessions') content = <SessionsView {...common} />;
  else if (navigation === 'verification') content = <VerificationView projection={bootstrap.projection} />;
  else content = <PanoramaView projection={bootstrap.projection} graph={bootstrap.graph} capabilities={bootstrap.capabilities} onViewpoint={changeViewpoint} />;

  return (
    <div className="app-shell">
      <TopBar projectName={bootstrap.graph.project.name} serviceVersion={bootstrap.service.version} />
      <NavigationRail current={navigation} onSelect={setNavigation} />
      <div className="app-content">
        {error ? <div className="error-banner" role="alert"><AlertTriangle size={17} />{error}<button type="button" onClick={() => setError(null)}>关闭</button></div> : null}
        {content}
      </div>
    </div>
  );
}
