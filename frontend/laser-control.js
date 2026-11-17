/* Modular dashboard. Machine capability flags never originate in the browser. */
(() => {
  if (document.body?.dataset.laserControl === 'disabled') return;
  let job = null, profiles = [], formKey = '', dirty = false, busy = false, refreshing = false;
  let jobControls = {actions:{}}, stopPending = false, layoutSupported = false;
  let supervised = {active:null,recent:[]};
  const route = '/api/laser-control';
  const note = (text, error = false) => { $('lcMessage').textContent = text; $('lcMessage').className = error ? 'lc-error' : 'hint'; };
  const value = id => $(id).value === '' ? null : Number($(id).value);
  const stamp = text => text ? new Date(text).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }) : '—';
  const cell = (row, text) => { const td = document.createElement('td'); td.textContent = text; row.append(td); return td; };

  function permissions() {
    document.querySelectorAll('[data-lc-permission]').forEach(button => {
      button.disabled = busy || !hasPermission(button.dataset.lcPermission);
    });
    for (const id of ['lcPrepare', 'lcFallback']) $(id).disabled = busy || !hasPermission('operate') || !job?.validation.valid;
    $('lcParametersForm').querySelectorAll('input').forEach(input => { input.disabled = busy || !job || !hasPermission('operate'); });
    $('lcLoadProfile').disabled = busy || !job || !hasPermission('operate');
    $('lcValidate').disabled = busy || !job || !hasPermission('operate');
    $('lcDiscardDraft').disabled = busy || !dirty || !hasPermission('operate');
    $('lcDownload').hidden = !job;
    if (job) $('lcDownload').href = `${route}/jobs/${encodeURIComponent(job.id)}/file`;
    else $('lcDownload').removeAttribute?.('href');
    document.querySelectorAll('[data-lc-fallback-action]').forEach(button => {
      const action = button.dataset.lcFallbackAction;
      button.disabled = !hasPermission('operate') || !jobControls.actions?.[action] || (action === 'stop' ? stopPending : busy);
      if (action === 'pause' && $('lcObservedState').value !== 'running') button.disabled = true;
      if (action === 'resume' && $('lcObservedState').value !== 'paused') button.disabled = true;
      if (supervised.active && action !== 'stop') {
        if (supervised.active.job_id !== job?.id) button.disabled = true;
        const cutting = ['start_pending','awaiting_completion','interrupted'].includes(supervised.active.state);
        if (cutting && !['pause','resume'].includes(action)) button.disabled = true;
        if (action === 'start' && (supervised.active.state !== 'reviewed' || supervised.active.review?.revision !== job?.revision)) button.disabled = true;
      }
      if (!supervised.active && action === 'start' && supervised.recent?.some(c=>c.job_id === job?.id)) button.disabled = true;
    });
    const cycle = supervised.active, operate = hasPermission('operate') && !busy;
    $('lcCycleReady').disabled = !operate || !!cycle || !window.beginSupervisedCycle;
    $('lcCycleReview').disabled = !operate || dirty || !cycle || !['awaiting_review','reviewed'].includes(cycle.state) || cycle.job_id !== job?.id || !jobControls.matching_job;
    $('lcCycleComplete').disabled = !operate || !cycle || !['start_pending','awaiting_completion'].includes(cycle.state);
    $('lcCycleAbort').disabled = !operate || !cycle || cycle.state === 'processing';
    $('lcSplit').disabled = busy || !layoutSupported || !jobControls.matching_job || !hasPermission('operate');
    $('lcRestore').disabled = busy || !layoutSupported || !hasPermission('operate');
    // No protocol contract is commissioned. Configuration never unlocks commands.
    document.querySelectorAll('[data-lc-command]').forEach(button => {
      button.disabled = true;
      button.title = 'No verified Ethernet command contract is installed. No machine command will be sent.';
    });
  }

  function fillParameters(parameters) {
    const fields = {lcMaterial:'material',lcThickness:'thickness_mm',lcSpeed:'speed_mm_s',lcMaxPower:'max_power_percent',lcMinPower:'min_power_percent',lcPasses:'passes'};
    for (const [id, key] of Object.entries(fields)) $(id).value = parameters[key] ?? '';
    $('lcLayers').replaceChildren();
    for (const layer of job?.validation.layers || []) {
      const settings = parameters.layers?.[layer.name] || {enabled:layer.default_enabled};
      const row = document.createElement('tr'); row.dataset.layer = layer.name;
      const enabled = document.createElement('input'); enabled.type = 'checkbox'; enabled.checked = settings.enabled !== false;
      enabled.dataset.field = 'enabled'; enabled.setAttribute('aria-label', `Cut layer ${layer.name}`);
      cell(row, '').append(enabled); cell(row, layer.name);
      for (const key of ['speed_mm_s','max_power_percent','min_power_percent','passes']) {
        const input = document.createElement('input'); input.type = 'number'; input.dataset.field = key;
        input.setAttribute('aria-label', `${layer.name} ${key}`); input.value = settings[key] ?? '';
        input.min = key === 'passes' ? '1' : key === 'speed_mm_s' ? '0.001' : '0';
        input.max = key === 'passes' || key.includes('power') ? '100' : '10000'; input.step = key === 'passes' ? '1' : 'any';
        input.placeholder = 'Inherit'; cell(row, '').append(input);
      }
      $('lcLayers').append(row);
    }
    permissions();
  }

  function parameters() {
    const layers = {};
    document.querySelectorAll('#lcLayers tr').forEach(row => {
      const layer = {};
      row.querySelectorAll('input').forEach(input => {
        layer[input.dataset.field] = input.type === 'checkbox' ? input.checked : input.value === '' ? null : Number(input.value);
      });
      // Empty per-layer passes inherit the global value rather than defaulting to one.
      if (layer.passes === null) layer.passes = value('lcPasses');
      layers[row.dataset.layer] = layer;
    });
    return {material:$('lcMaterial').value,thickness_mm:value('lcThickness'),speed_mm_s:value('lcSpeed'),
      max_power_percent:value('lcMaxPower'),min_power_percent:value('lcMinPower'),passes:value('lcPasses'),layers};
  }

  function drawPreview() {
    const canvas = $('lcPreview'), context = canvas.getContext('2d');
    const width = Math.max(200, canvas.clientWidth || 600), height = 300;
    const ratio = window.devicePixelRatio || 1; canvas.width = width*ratio; canvas.height = height*ratio;
    context.scale(ratio, ratio); context.clearRect(0,0,width,height);
    const paths = job?.validation.preview || [], bounds = job?.validation.bounds_mm;
    if (!bounds || !paths.length) { context.fillStyle = '#9db0b6'; context.font = '14px Segoe UI'; context.fillText('The selected DXF layout will appear here', 22, 40); return; }
    const w = Math.max(1, bounds.max_x-bounds.min_x), h = Math.max(1, bounds.max_y-bounds.min_y);
    const scale = Math.min((width-44)/w,(height-44)/h);
    const left = (width-w*scale)/2, top = (height-h*scale)/2;
    context.lineWidth = 1.4;
    for (const path of paths) {
      context.strokeStyle = job.parameters.layers?.[path.layer]?.enabled === false ? '#66787d' : job.validation.valid ? '#80e0bb' : '#ff9989';
      context.beginPath();
      path.points.forEach(([x,y], i) => {
        const px = left+(x-bounds.min_x)*scale, py = top+(bounds.max_y-y)*scale;
        if (i) context.lineTo(px,py); else context.moveTo(px,py);
      });
      if (path.closed) context.closePath(); context.stroke();
    }
  }

  function renderJob(next) {
    if (dirty && job && next && (next.id !== job.id || next.revision !== job.revision)) {
      note('A different job or revision is now selected. Refresh the dashboard to discard this unsaved draft and review it.', true);
      return;
    }
    job = next;
    $('lcFilename').textContent = job?.filename || 'Waiting for a DXF';
    $('lcJobState').textContent = job?.status.replaceAll('_',' ') || 'No job';
    const size = job?.validation.dimensions_mm;
    $('lcJobInfo').textContent = job ? `${size ? size.width.toFixed(2)+' × '+size.height.toFixed(2)+' mm • ' : ''}${job.validation.entity_count} entities • Revision ${job.revision} • ${job.source}` : 'Generated DXFs arrive automatically after export.';
    $('lcValidation').replaceChildren();
    for (const [messages, kind] of [[job?.validation.errors || [],'lc-error'],[job?.validation.warnings || [],'hint']]) {
      messages.forEach(text => { const p = document.createElement('p'); p.className = kind; p.textContent = text; $('lcValidation').append(p); });
    }
    if (job?.validation.valid) { const p = document.createElement('p'); p.className = 'lc-ok'; p.textContent = 'DXF geometry validated. Export coordinates are preserved; machine travel limits are not inferred here.'; $('lcValidation').prepend(p); }
    const key = job ? `${job.id}:${job.revision}` : '';
    if (key !== formKey) { formKey = key; if (job) fillParameters(job.parameters); }
    $('lcPreparation').textContent = job?.prepared ? 'Draft frozen for conversion. Controller encoding and transfer are not available.' : 'No controller-compatible job has been generated.';
    drawPreview(); permissions();
  }

  async function refresh() {
    if (refreshing || !state.user || state.activeView !== 'laser-control' || document.hidden) return;
    refreshing = true;
    try {
      const data = await api(route+'/status', {cache:'no-store'});
      supervised = data.supervised || {active:null,recent:[]};
      const cycle = supervised.active;
      const stages = {processing:'Preparing DXF',awaiting_review:'Review LaserCAD',reviewed:'Awaiting confirmed Start',start_pending:'Start outcome unconfirmed',awaiting_completion:'Awaiting operator completion',interrupted:'Interrupted — verify machine stopped'};
      $('lcCycleState').textContent = cycle ? stages[cycle.state] || cycle.state : 'Load sheath';
      $('lcCycleHelp').textContent = cycle ? (cycle.error || (cycle.state === 'processing'
        ? 'Capture, calibration, nesting and DXF preparation are in progress. Cutting has not been started by this cycle.'
        : ['awaiting_review','reviewed'].includes(cycle.state) ? 'Check the layout and actual settings in LaserCAD, download the intended job, and confirm review. Then use Start cutting now (LaserCAD) below. No popup approval appears.'
        : 'Observe the machine. A panel request is not controller feedback. Confirm completion only after cutting has finished and the machine has stopped.'))
        : 'Load the next sheath while the machine is stopped, then press Sheath loaded — Prepare job. No capture starts automatically.';
      $('lcCycles').replaceChildren();
      for (const item of supervised.recent || []) {
        const row = document.createElement('tr');
        cell(row,item.filename || item.id);cell(row,item.state.replaceAll('_',' '));cell(row,stamp(item.start_requested_at));cell(row,stamp(item.operator_completed_at));$('lcCycles').append(row);
      }
      layoutSupported = !!data.desktop_layout_supported;
      $('lcLayoutStatus').textContent = (data.split_after_dxf === false ? 'Automatic split is off. ' : 'Automatic split after DXF export is on. ') + (data.desktop_layout_status?.message || 'Use Split screen to arrange both windows.');
      $('lcLayoutStatus').className = data.desktop_layout_status?.status === 'unavailable' ? 'lc-error' : 'hint';
      $('lcAddress').textContent = data.controller.ip;
      if (document.activeElement !== $('lcIp')) $('lcIp').value = data.controller.ip;
      $('lcBlocker').textContent = data.controller.blocker;
      $('lcConnection').textContent = 'Unverified • no controller response';
      if (job?.id !== data.job?.id) { jobControls={actions:{}}; $('lcObservedState').value=''; }
      renderJob(data.job);
      const [history, saved, controls] = await Promise.all([api(route+'/jobs'),api(route+'/profiles'),api(route+'/job-controls')]);
      jobControls = controls;
      $('lcJobControlStatus').textContent = jobControls.available
        ? jobControls.matching_job ? 'Matching LaserCAD job detected. Actions request panel clicks; controller execution is not confirmed.' : 'LaserCAD panel detected. Open the selected DXF there to enable Download and Run Box.'
        : (jobControls.panel_message || 'LaserCAD panel not detected.') + ' Open this DXF in LaserCAD and refresh to enable its available controls.';
      if (jobControls.available) $('lcJobControlStatus').textContent += ' LaserCAD selection: ' + (jobControls.communication_selection?.join(' / ') || 'unavailable; review in LaserCAD') + '. Pause and Resume both use its Pause/Continue toggle.';
      permissions();
      profiles = saved.profiles;
      const selectedProfile = $('lcProfiles').value;
      $('lcProfiles').replaceChildren(new Option('Choose a saved profile',''));
      profiles.forEach((profile, index) => $('lcProfiles').append(new Option(profile.name,String(index))));
      $('lcProfiles').value = selectedProfile;
      $('lcHistory').replaceChildren();
      for (const rowJob of history.jobs) {
        const row = document.createElement('tr');
        cell(row, `${rowJob.filename} / ${rowJob.parameters.material}, ${rowJob.parameters.thickness_mm} mm`);
        cell(row, rowJob.status.replaceAll('_',' ')); cell(row,stamp(rowJob.created_at));
        cell(row,stamp(rowJob.started_at)); cell(row,`${stamp(rowJob.completed_at)} / ${rowJob.execution_seconds ?? '—'}`);
        const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Review';
        button.disabled = !hasPermission('operate') || busy;
        button.onclick = () => action(async () => { if (dirty) { note('Save the parameter draft or use Discard draft first.',true); return; } dirty = false; await api(`${route}/jobs/${rowJob.id}/select`, {method:'POST',body:'{}'}); });
        cell(row,'').append(button); $('lcHistory').append(row);
      }
      if (job) {
        const events = await api(`${route}/jobs/${job.id}/events`);
        $('lcEvents').replaceChildren();
        events.events.forEach(event => { const p = document.createElement('p'); p.textContent = `${stamp(event.timestamp)} • ${event.kind.replaceAll('_',' ')} • ${JSON.stringify(event.detail)}`; $('lcEvents').append(p); });
      }
    } catch (error) { note(displayError(error),true); }
    finally { refreshing = false; }
  }

  async function action(work) {
    if (busy) return;
    busy = true; permissions();
    try { await work(); } catch (error) { note(displayError(error),true); }
    finally { busy = false; permissions(); await refresh(); }
  }

  $('lcRefresh').onclick = () => { if (dirty) { note('Save the parameter draft or use Discard draft first.',true); return; } dirty = false; formKey = ''; void refresh(); };
  $('lcParametersForm').oninput = () => { dirty = true; permissions(); };
  $('lcObservedState').onchange = permissions;
  $('lcDiscardDraft').onclick = () => { dirty=false;formKey='';void refresh(); };
  $('lcParametersForm').onsubmit = event => { event.preventDefault(); void action(async () => {
    if (!job) throw new Error('Select a DXF first.');
    await api(`${route}/jobs/${job.id}/parameters`,{method:'POST',body:JSON.stringify({revision:job.revision,parameters:parameters()})});
    dirty = false; note('Job parameters saved. No machine parameters were changed.');
  }); };
  $('lcUploadForm').onsubmit = event => { event.preventDefault(); void action(async () => {
    if (dirty) { note('Save the parameter draft or use Discard draft first.',true); return; }
    const file = $('lcUploadFile').files[0]; if (!file) throw new Error('Select a DXF first.');
    const form = new FormData(); form.append('file',file);
    await api(route+'/jobs/upload',{method:'POST',body:form}); dirty = false; note('DXF received. Review the validation and layer settings.');
  }); };
  $('lcPrepare').onclick = () => action(async () => {
    if (dirty) throw new Error('Save the parameter draft before preparing.');
    await api(`${route}/jobs/${job.id}/prepare`,{method:'POST',body:JSON.stringify({revision:job.revision})});
    note('Draft frozen. Direct job encoding and transfer remain blocked pending verified controller support.');
  });
  $('lcFallback').onclick = () => action(async () => {
    if (dirty) throw new Error('Save or discard the parameter draft before handoff.');
    const result = await api(`${route}/jobs/${job.id}/fallback`,{method:'POST',body:'{}'});
    note(`${result.message} ${result.handoff.message || ''}`,result.handoff.status !== 'launch_requested');
    if (result.handoff.status === 'launch_requested') navigateSection('lasercad');
  });
  $('lcLegacyControls').onclick = () => navigateSection('export');
  $('lcCycleReady').onclick = () => { if (!$('lcCycleReady').disabled) void window.beginSupervisedCycle(); };
  $('lcCycleReview').onclick = () => action(async () => {
    if (!supervised.active || !job || dirty) throw new Error('Save the draft and review the active cycle first.');
    const payload = {cycle_id:supervised.active.id,operator_confirmed:true,revision:job.revision,dxf_sha256:job.sha256,
      window_title:jobControls.window_title,communication_selection:[...(jobControls.communication_selection || [])]};
    await api(route+'/cycle/review',{method:'POST',body:JSON.stringify(payload)});
    note('Review recorded. Press Start cutting now (LaserCAD) when ready. That button sends Start immediately, with no popup.');
  });
  for (const [id,outcome] of [['lcCycleComplete','completed'],['lcCycleAbort','aborted']]) {
    $(id).onclick = () => action(async () => {
      if (!supervised.active) throw new Error('No active supervised cycle.');
      const cycleId = supervised.active.id;
      await api(route+'/cycle/confirm',{method:'POST',body:JSON.stringify({cycle_id:cycleId,outcome,operator_confirmed:true,machine_stopped:true})});
      note(outcome === 'completed' ? 'Cutting completion recorded as operator-reported. Load the next sheath when ready.' : 'Cycle aborted by operator confirmation.');
    });
  }
  for (const [id, layout] of [['lcSplit','split'],['lcRestore','restore']]) {
    $(id).onclick = () => action(async () => {
      const result = await api(route+'/layout', {method:'POST',body:JSON.stringify({action:layout})});
      note(result.message);
    });
  }
  $('lcValidate').onclick = () => action(async () => {
    const value = await api(`${route}/jobs/${job.id}/validate`, {method:'POST',body:'{}'});
    note(value.validation.valid ? 'Job geometry revalidated. Coordinates remain unchanged.' : 'Review the job validation errors.', !value.validation.valid);
  });
  document.querySelectorAll('[data-lc-fallback-action]').forEach(button => {
    button.onclick = () => {
      const name = button.dataset.lcFallbackAction;
      if (button.disabled || !hasPermission('operate')) return;
      const send = async () => {
        if (dirty && name !== 'stop') throw new Error('Save or discard the parameter draft before a machine-panel request.');
        const payload = {action:name,job_id:job?.id || null,request_id:crypto.randomUUID()};
        if (['start','pause','resume'].includes(name)) {
          if (!job || !jobControls.matching_job) throw new Error('Refresh and review the matching LaserCAD job first.');
          Object.assign(payload, {revision:job.revision,dxf_sha256:job.sha256,window_title:jobControls.window_title,
            communication_selection:[...(jobControls.communication_selection || [])],operator_confirmed:true,
            observed_state:['pause','resume'].includes(name) ? $('lcObservedState').value : null});

        }
        if (name === 'pause' && payload.observed_state !== 'running') throw new Error('Observe the machine and select Running before Pause.');
        if (name === 'resume' && payload.observed_state !== 'paused') throw new Error('Observe the machine and select Paused before Resume.');
        // A timeout can still deliver a toggle. Require a fresh observation
        // before any subsequent Pause/Resume, even when this request fails.
        if (['pause','resume','stop'].includes(name)) { $('lcObservedState').value = ''; permissions(); }
        const result = await api(route+(name === 'stop' ? '/fallback-stop' : '/fallback-command'), {
          method:'POST', body:JSON.stringify(payload)
        });
        note(result.message);
      };
      if (name !== 'stop') { void action(send); return; }
      // Stop remains usable while another job operation is pending.
      stopPending = true; permissions();
      void send().catch(error => note(displayError(error),true)).finally(() => {stopPending=false;permissions();void refresh();});
    };
  });
  $('lcSaveProfile').onclick = () => action(async () => {
    await api(route+'/profiles',{method:'POST',body:JSON.stringify({name:$('lcProfileName').value,parameters:parameters()})});
    note('Material profile saved. It does not establish a tested cutting recipe.');
  });
  $('lcLoadProfile').onclick = () => {
    const profile = profiles[Number($('lcProfiles').value)];
    if ($('lcProfiles').value === '' || !profile || !job) return;
    fillParameters(profile.parameters); dirty = true; note('Profile loaded into the draft. Review layers and save job parameters.');
  };
  $('lcAddressForm').onsubmit = event => { event.preventDefault(); void action(async () => {
    const config = await api('/api/config'); config.laser_control = {...config.laser_control,controller_ip:$('lcIp').value};
    await api('/api/config',{method:'POST',body:JSON.stringify(config)}); note('Controller address saved. Connection remains unverified.');
  }); };
  window.LaserControl = {refresh, hasActiveCycle:() => !!supervised.active, receiveExport() { dirty = false; formKey = ''; $('lcObservedState').value=''; jobControls={actions:{}};permissions(); }, reset() { job = null; formKey = ''; dirty = false;$('lcObservedState').value='';supervised={active:null,recent:[]};jobControls={actions:{}}; $('lcHistory').replaceChildren(); $('lcEvents').replaceChildren();$('lcCycles').replaceChildren(); renderJob(null); }};
  window.addEventListener('resize',() => { if (state.activeView === 'laser-control') drawPreview(); });
  const poll = setInterval(() => { if (state.activeView === 'laser-control' && state.user && !document.hidden && !busy) void refresh(); },4000);
  window.addEventListener('pagehide',() => clearInterval(poll));
  permissions();
})();
