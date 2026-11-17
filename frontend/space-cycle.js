/* Operator-paced sequence; every press authorizes exactly one stage. */
(() => {
  let current = null, pending = false, pollPending = false, held = new Set(), preparingCamera = false;
  const endpoint = '/api/laser-control/space-cycle';
  const labels = {ready_process:'Space 1: Refresh camera → Capture → Download Document',
    processing:'Space 1: Processing — wait',
    ready_start:'Space 2: Start',start_pending:'Start request pending — wait',
    awaiting_manual_completion:'Manual LaserCAD controls · Space when cutting is finished',
    awaiting_completion:'Space 3: Confirm completion → Process Sheath',interrupted:'Cycle interrupted · inspect machine before reset'};
  const bar=document.createElement('div');bar.id='spaceCycleBar';bar.hidden=true;
  const title=document.createElement('strong'), note=document.createElement('span');note.setAttribute('role','status');
  const next=document.createElement('button'), done=document.createElement('button'), reset=document.createElement('button');
  next.id='automaticWorkflowAction';
  next.type=done.type=reset.type='button';done.textContent='Confirm cutting completed (Space)';reset.textContent='Machine idle — reset cycle';
  bar.append(title,note,next,done,reset);
  const processHost=document.querySelector('.cycle-dashboard');processHost.prepend(bar);
  function decorate() {
    if (!current) return;
    const text=preparingCamera ? 'Space 1: Refreshing camera — wait' : labels[current.stage] || current.stage;
    for (const id of ['workflowStatus','cycleNextHelp','processStageDetail']) { const el=$(id);if(el)el.textContent=preparingCamera ? text : current.message || text; }
    if ($('cycleState')) $('cycleState').textContent=text;
  }
  function render() {
    const allowed=!!state.user && hasPermission('operate');
    const laser=state.activeView==='lasercad', process=state.activeView==='process';
    const parent=laser ? $('laserCadTab') : processHost;
    if(bar.parentNode!==parent)parent.prepend(bar);
    bar.hidden=!allowed || (!laser && !process);
    title.textContent=preparingCamera ? 'Space 1: Refreshing camera — wait' : labels[current?.stage] || 'Loading Space workflow…';
    note.textContent=preparingCamera ? 'Reconnecting the camera before capture. Please wait.' : current?.message || 'Press Space to refresh the camera, capture and process through Download Document.';
    next.textContent=current?.next_action || 'Wait';next.disabled=pending || !current?.next_action;
    const reference=$('automaticWorkflow');
    if(reference) { reference.hidden=!allowed;reference.textContent='Automatic workflow';reference.title=labels[current?.stage] || 'Show automatic workflow status'; }
    done.hidden=!['awaiting_completion','awaiting_manual_completion'].includes(current?.stage);reset.hidden=current?.stage!=='interrupted';
    done.disabled=reset.disabled=pending;
    const datum=$('manualDatum');
    if(datum)datum.disabled=!allowed || pending || state.workflowBusy || state.pendingMutations || state.cameraRefreshing || datum.dataset?.pending==='true' || !['ready_process','interrupted'].includes(current?.stage);
    decorate();
  }
  async function refresh() {
    if(pollPending || !state.user || !hasPermission('operate')) {render();return;}
    pollPending=true;
    try{current=await api(endpoint,{cache:'no-store'});render();}catch(e){note.textContent=displayError(e);next.disabled=true;current=null;}
    finally{pollPending=false;}
  }
  async function advance() {
    if(pending || !current?.next_action || !hasPermission('operate'))return;
    if(state.workflowBusy || state.pendingMutations || state.cameraRefreshing) {
      const text='Another operation is active. Wait for it to finish, then press Space again. No stage was triggered.';
      note.textContent=text;message(text,true);return;
    }
    const snapshot={...current};pending=true;render();
    try{
      if(snapshot.stage==='ready_process') {
        preparingCamera=true;render();
        // The native host may still have a display POST in flight. Hide it and
        // wait for UI mutations before calling the mandatory camera reconnect.
        await window.LaserCADTab?.hide();
        $('workflowFormat').value='dxf';navigateSection('process');
        const deadline=Date.now()+10000;
        while(state.pendingMutations || state.cameraRefreshing) {
          if(Date.now()>=deadline)throw new Error('The display/camera is still busy. Wait, then press Space again.');
          await new Promise(resolve=>setTimeout(resolve,50));
        }
        if(await refreshCamera({required:true})!==true)throw new Error('Camera refresh did not complete. Capture was not started; press Space to retry.');
        preparingCamera=false;render();
        await runWorkflow(snapshot);
      } else {
        // Expose the native tab so a newly launched LaserCAD can be hosted.
        navigateSection('lasercad');
        current=await api(endpoint+'/action',{method:'POST',body:JSON.stringify({cycle_id:snapshot.cycle_id,revision:snapshot.revision,expected_stage:snapshot.stage,request_id:crypto.randomUUID()})});
      }
    }catch(e){message(displayError(e),true);}
    finally{preparingCamera=false;pending=false;await refresh();}
  }
  async function finish(completed, {confirmedBySpace = false, expectedSnapshot = null} = {}) {
    if(pending || !current || !state.user || !hasPermission('operate') || state.workflowBusy || state.pendingMutations)return;
    const snapshot={...(expectedSnapshot || current)};
    if(snapshot.cycle_id!==current.cycle_id || !(completed ? ['awaiting_completion','awaiting_manual_completion'] : ['interrupted']).includes(snapshot.stage))return;
    pending=true;render();
    try{
      // A deliberate Space after cutting is the operator's completion/idle acknowledgement.
      // Mouse completion and interrupted-cycle recovery retain their confirmation.
      if((!completed || !confirmedBySpace) && !confirm(completed ? 'Has cutting finished and is the machine now idle? Confirming resets the cycle. The next Space press captures the next sheath.' : 'Have you inspected the interrupted request and confirmed the machine is idle? Reset does not stop the machine.'))return;
      current=await api(endpoint+'/reset',{method:'POST',body:JSON.stringify({cycle_id:snapshot.cycle_id,revision:snapshot.revision,completed,operator_confirmed_idle:true})});
      // Publish completion before navigating. Navigation alone never confirms a cut.
      await window.LaserCADTab?.hide();
      $('processSheath').click();
      if(confirmedBySpace) $('overlay')?.focus({preventScroll:true});
      return {status:'reset',stage:current.stage};
    }catch(e){message(displayError(e),true);}
    finally{
      // Keep the browser key held until key-up: reset must not reuse the same
      // physical press for the next capture. Native presses are edge-filtered.
      pending=false;await refresh();
    }
  }
  const completionKey=code=>code==='Space';
  function accepts(code) {
    return code==='Space' && ['ready_process','ready_start'].includes(current?.stage);
  }
  function press(code) {
    if(completionKey(code) && ['awaiting_completion','awaiting_manual_completion'].includes(current?.stage))void finish(true,{confirmedBySpace:true});
    else if(accepts(code))void advance();
  }
  function keydown(event, actionButton = false) {
    if(event.code!=='Space')return;
    if(event.repeat || held.has(event.code) || event.defaultPrevented || event.isComposing || event.ctrlKey || event.altKey || event.shiftKey || event.metaKey || document.hidden || !document.hasFocus())return;
    if(!['process','lasercad'].includes(state.activeView) || state.editBoundary || !hasPermission('operate'))return;
    const owns=node=>node?.closest?.('input,textarea,select,button,a,summary,[contenteditable]:not([contenteditable="false"]),[role="textbox"],[role="combobox"],[role="button"],[role="slider"],[role="checkbox"],[role="listbox"],[role="menuitem"],[role="radio"],[role="switch"],[role="tab"],[role="option"],[role="treeitem"],dialog');
    if(!actionButton && (owns(event.target)||owns(document.activeElement)))return;
    held.add(event.code);event.preventDefault();press(event.code);
  }
  document.addEventListener('keyup',e=>held.delete(e.code));window.addEventListener('blur',()=>held.clear());
  // The explicit workflow action uses Space only; Enter cannot activate Start.
  for(const button of [next])button?.addEventListener('keydown',e=>{
    if(['Enter','NumpadEnter'].includes(e.code)) { e.preventDefault(); return; }
    if(e.code==='Space') {
      if(e.repeat || held.has(e.code)){e.preventDefault();return;}
      keydown(e,true);
    }
  });
  done.addEventListener('keydown',e=>{
    if(['Enter','NumpadEnter'].includes(e.code)){e.preventDefault();return;}
    if(completionKey(e.code)) {
      if(e.repeat || held.has(e.code)){e.preventDefault();return;}
      keydown(e,true);
    }
  });
  // A navigation button can retain focus after returning from the native pane.
  // Handle its completion Space explicitly instead of silently ignoring it.
  $('processSheath')?.addEventListener('keydown',e=>{
    if(e.code==='Space' && ['awaiting_completion','awaiting_manual_completion'].includes(current?.stage)) {
      if(e.repeat || held.has(e.code)){e.preventDefault();return;}
      keydown(e,true);
    }
  });
  $('automaticWorkflow')?.addEventListener('click',()=>{navigateSection('process');render();});
  next.addEventListener('click',()=>void advance());done.addEventListener('click',()=>void finish(true));reset.addEventListener('click',()=>void finish(false));
  async function nativeComplete(snapshot) {
    // A native completion edge is pinned to its cycle/revision. It cannot call
    // advance(), even if polling or a delayed callback sees a different stage.
    if(state.activeView!=='lasercad' || document.hidden || !snapshot || !['awaiting_completion','awaiting_manual_completion'].includes(snapshot.stage))return {status:'ignored'};
    held.delete('Space');
    return await finish(true,{confirmedBySpace:true,expectedSnapshot:snapshot}) || {status:'blocked'};
  }
  window.SpaceCycle={advance,keydown,decorate,nativeComplete,nativePress:(code)=>{if(code==='Space' && state.activeView==='lasercad' && !document.hidden){held.delete('Space');press(code);}}};
  setInterval(()=>void refresh(),750);void refresh();
})();
