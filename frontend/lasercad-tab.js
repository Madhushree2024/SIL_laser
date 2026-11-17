/* The native host displays the real application; it never sends job commands. */
(() => {
  let pending = false, active = false, retryUntil = 0, stopped = false;
  const endpoint = '/api/laser-control/lasercad-host';
  const status = text => { $('laserCadTabStatus').textContent = text; };
  let infoPending = false, infoUpdated = 0;
  async function refreshInfo(force = false) {
    if (infoPending || (!force && Date.now() - infoUpdated < 5000)) return;
    infoPending = true;
    try {
      const info = await api('/api/application-info', {cache:'no-store'});
      renderApplicationVersion(info);
      $('applicationPath').textContent = info.app_path;
      $('laserCadSelectedPath').textContent = info.resolved_lasercad_path || 'Not found — select LaserCAD in Settings.';
      $('laserCadRunningPath').textContent = info.running_lasercad_paths.join('\n') || 'No running LaserCAD window detected.';
      const messages = {
        matching: 'Running LaserCAD matches the selected installation.',
        different: 'A different LaserCAD installation is running. Close it normally and open the drawing from this app.',
        multiple: 'Multiple LaserCAD installations are running. Close the extra instances normally.',
        not_running: 'LaserCAD has not opened yet.',
        unavailable: 'Could not check the running LaserCAD path.'
      };
      $('laserCadPathStatus').textContent = messages[info.lasercad_state] || 'Path check unavailable.';
      $('laserCadPathStatus').dataset.state = info.lasercad_state;
      infoUpdated = Date.now();
    } catch (error) {
      $('laserCadPathStatus').textContent = 'Installation details unavailable: ' + displayError(error);
    } finally { infoPending = false; }
  }
  $('applicationInfo').addEventListener('toggle', () => {
    if ($('applicationInfo').open) { void refreshInfo(true); if (active) void refresh(); }
  });
  async function refresh() {
    if (!active || pending || stopped || document.hidden || !state.user || state.activeView !== 'lasercad') return;
    if (!hasPermission('operate')) { status('Operator access is required to use LaserCAD.'); return; }
    void refreshInfo();
    const rect = $('laserCadNativeSlot').getBoundingClientRect();
    const x = Math.max(0, rect.left), y = Math.max(0, rect.top);
    const width = Math.min(innerWidth, rect.right) - x, height = Math.min(innerHeight, rect.bottom) - y;
    if (width < 100 || height < 100) return;
    pending = true;
    try {
      const result = await api(endpoint, {method:'POST',body:JSON.stringify({action:'show',x,y,width,height,viewport_width:innerWidth,viewport_height:innerHeight})});
      if (!active || state.activeView !== 'lasercad') { void hide(); return; }
      const preparation = result.automation;
      const matches = preparation?.path && result.title?.includes(preparation.path.split(/[\\/]/).pop());
      status(matches && preparation.message ? preparation.message : result.title ? 'LaserCAD — ' + result.title : result.message);
      $('laserCadTabStatus').dataset.transfer = matches ? preparation.status : '';
      $('laserCadTabStatus').title = $('laserCadTabStatus').textContent;
      $('laserCadNativeSlot').dataset.hosted = String(result.status === 'hosted');
      $('laserCadNativeHint').hidden = result.status === 'hosted';
    } catch (error) {
      status(displayError(error));
      $('laserCadNativeSlot').dataset.hosted = 'false';
      $('laserCadNativeHint').hidden = false;
      if (/scaling|desktop application/i.test(displayError(error)) || Date.now() > retryUntil) stopped = true;
    } finally { pending = false; }
  }
  async function hide() {
    const wasActive = active; active = false;
    if (!wasActive || !state.user || !hasPermission('operate')) return;
    try { await api(endpoint,{method:'POST',body:JSON.stringify({action:'hide'})}); } catch (_) { /* Host lease expires independently. */ }
  }
  function activate() {
    active = true; stopped = false; retryUntil = Date.now() + 45000;
    status('Waiting for the current LaserCAD drawing…');
    void refresh();
  }
  $('laserCadTabRetry').onclick = activate;
  $('laserCadTabBack').onclick = () => navigateSection('process');
  $('laserCadTabOpen').onclick = async () => {
    if (!hasPermission('operate')) return;
    $('laserCadTabOpen').disabled = true;
    try {
      const current = await api('/api/laser-control/status', {cache:'no-store'});
      if (current.job) await api('/api/laser-control/jobs/' + encodeURIComponent(current.job.id) + '/fallback',{method:'POST',body:'{}'});
      else await api('/api/laser/control/open',{method:'POST',body:'{}'});
      activate();
    } catch (error) { status(displayError(error)); }
    finally { $('laserCadTabOpen').disabled = false; }
  };
  $('laserCadTabDetach').onclick = async () => {
    await hide();
    try { const result=await api(endpoint,{method:'POST',body:JSON.stringify({action:'detach'})});status(result.message); }
    catch (error) { status(displayError(error)); }
    $('laserCadNativeHint').hidden=false;
  };
  window.LaserCADTab = {activate, hide};
  window.addEventListener('resize', () => { if (active) void refresh(); });
  window.addEventListener('scroll', () => { if (active) void refresh(); });
  document.addEventListener('visibilitychange', () => { if (document.hidden) void hide(); else if (state.activeView === 'lasercad') activate(); });
  const timer = setInterval(refresh, 900);
  window.addEventListener('pagehide', () => { clearInterval(timer); void hide(); });
})();
