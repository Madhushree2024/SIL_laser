/* Operator-paced sequence; every press authorizes exactly one stage. */
(() => {
  let current = null, pending = false, pollPending = false, held = new Set(), preparingCamera = false;
  let completionRecovery = null, statusEpoch = 0;
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
    const text=completionRecovery ? completionRecovery.message : preparingCamera ? 'Space 1: Refreshing camera — wait' : labels[current.stage] || current.stage;
    for (const id of ['workflowStatus','cycleNextHelp','processStageDetail']) { const el=$(id);if(el)el.textContent=completionRecovery || preparingCamera ? text : current.message || text; }
    if ($('cycleState')) $('cycleState').textContent=text;
  }
  function render() {
    const allowed=!!state.user && hasPermission('operate');
    const laser=state.activeView==='lasercad', process=state.activeView==='process';
    const parent=laser ? $('laserCadTab') : processHost;
    if(bar.parentNode!==parent)parent.prepend(bar);
    bar.hidden=!allowed || (!laser && !process);
    title.textContent=completionRecovery ? completionRecovery.message : preparingCamera ? 'Space 1: Refreshing camera — wait' : labels[current?.stage] || 'Loading Space workflow…';
    note.textContent=completionRecovery ? completionRecovery.message : preparingCamera ? 'Reconnecting the camera before capture. Please wait.' : current?.message || 'Press Space to refresh the camera, capture and process through Download Document.';
    next.textContent=completionRecovery ? (completionRecovery.committed ? 'Return to Process Sheath' : 'Retry completion') : current?.next_action || 'Wait';next.disabled=pending || (!completionRecovery && !current?.next_action);
    const reference=$('automaticWorkflow');
    if(reference) { reference.hidden=!allowed;reference.textContent='Automatic workflow';reference.title=labels[current?.stage] || 'Show automatic workflow status'; }
    done.hidden=!['awaiting_completion','awaiting_manual_completion'].includes(current?.stage);reset.hidden=current?.stage!=='interrupted';
    done.disabled=reset.disabled=pending;
    const datum=$('manualDatum');
    if(datum)datum.disabled=!allowed || !!completionRecovery || pending || state.workflowBusy || state.pendingMutations || state.cameraRefreshing || datum.dataset?.pending==='true' || !['ready_process','interrupted'].includes(current?.stage);
    decorate();
  }
  async function refresh() {
    if(pollPending || (completionRecovery && pending) || !state.user || !hasPermission('operate')) {render();return;}
    pollPending=true;const epoch=statusEpoch;
    try{const result=await api(endpoint,{cache:'no-store'});if(epoch===statusEpoch){current=result;render();}}
    catch(e){if(epoch===statusEpoch){note.textContent=displayError(e);next.disabled=true;if(!completionRecovery)current=null;}}
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
  function resetMatches(status, recovery) {
    const receipt=status?.reset_from, original=recovery.snapshot;
    return status?.stage==='ready_process' && status.job===null && status.cycle_id!==original.cycle_id &&
      receipt?.cycle_id===original.cycle_id && receipt.revision===original.revision &&
      receipt.completed===recovery.completed && receipt.operator_confirmed_idle===true;
  }
  function stillAwaitingReset(status, recovery) {
    const old=recovery.snapshot;
    return status?.cycle_id===old.cycle_id && status.revision===old.revision && status.stage===old.stage;
  }
  async function completionStatus() {
    ++statusEpoch;
    current=await api(endpoint,{cache:'no-store'});
    return current;
  }
  function processScreenReady() {
    return state.activeView==='process' && document.body?.dataset.screen==='process' &&
      $('processSheath')?.getAttribute('aria-pressed')==='true' && !$('laserCadTab')?.classList.contains('active');
  }
  async function returnToProcess(recovery) {
    // A display failure must not prevent the existing navigation handler.
    try{await window.LaserCADTab?.hide();}catch(e){message(displayError(e),true);}
    if(!recovery.clickAttempted) {
      const button=$('processSheath');
      if(!button || button.disabled || typeof button.onclick!=='function')throw new Error('Process Sheath is unavailable. Press Space to retry navigation only.');
      recovery.clickAttempted=true;
      try{button.click();}catch(e){recovery.navigationError=displayError(e);}
    }
    // Reuse the existing navigator only if the one button activation failed.
    // Never click/dispatch the Process Sheath handler a second time.
    if(!processScreenReady())navigateSection('process');
    if(!processScreenReady())throw new Error('Cycle reset is saved, but Process Sheath did not open. Press Space to retry navigation only.');
    render();
    if(recovery.confirmedBySpace)$('overlay')?.focus({preventScroll:true});
  }
  async function performCompletion(recovery, checkFirst) {
    if(checkFirst) {
      const status=await completionStatus();
      if(resetMatches(status,recovery))recovery.committed=true;
      else if(recovery.committed || !stillAwaitingReset(status,recovery))throw new Error('The cycle changed without a matching reset receipt. No reset or machine action was retried.');
    }
    if(!recovery.committed) {
      ++statusEpoch;
      try{
        const old=recovery.snapshot;
        current=await api(endpoint+'/reset',{method:'POST',body:JSON.stringify({cycle_id:old.cycle_id,revision:old.revision,completed:recovery.completed,operator_confirmed_idle:true})});
        if(!resetMatches(current,recovery))throw new Error('Reset response did not identify the completed cycle.');
        recovery.committed=true;
      }catch(error){
        // An error/timeout is not evidence that reset failed. Read the durable
        // receipt before allowing any later operator-authorized retry.
        const status=await completionStatus();
        if(resetMatches(status,recovery))recovery.committed=true;
        else throw new Error('Completion could not be verified: '+displayError(error)+'. Press Space to check status and retry completion; cutting will not restart.');
      }
    }
    recovery.message='Cycle reset saved — returning to Process Sheath';render();
    await returnToProcess(recovery);
    completionRecovery=null;++statusEpoch;render();
    return {status:'reset',stage:current.stage};
  }
  async function resumeCompletion() {
    if(pending || !completionRecovery || !state.user || !hasPermission('operate') || state.workflowBusy || state.pendingMutations)return;
    const recovery=completionRecovery;pending=true;render();
    try{return await performCompletion(recovery,true);}
    catch(error){recovery.message=displayError(error);message(recovery.message,true);return {status:'recovery_pending'};}
    finally{pending=false;await refresh();}
  }
  async function finish(completed, {confirmedBySpace = false, expectedSnapshot = null} = {}) {
    if(completionRecovery)return resumeCompletion();
    if(pending || !current || !state.user || !hasPermission('operate') || state.workflowBusy || state.pendingMutations)return;
    const snapshot={...(expectedSnapshot || current)};
    if(snapshot.cycle_id!==current.cycle_id || !(completed ? ['awaiting_completion','awaiting_manual_completion'] : ['interrupted']).includes(snapshot.stage))return;
    pending=true;render();
    try{
      if((!completed || !confirmedBySpace) && !confirm(completed ? 'Has cutting finished and is the machine now idle? Confirming resets the cycle. The next Space press captures the next sheath.' : 'Have you inspected the interrupted request and confirmed the machine is idle? Reset does not stop the machine.'))return;
      completionRecovery={snapshot,completed,confirmedBySpace,committed:false,clickAttempted:false,message:'Confirming cycle completion'};
      ++statusEpoch;
      return await performCompletion(completionRecovery,false);
    }catch(error){
      if(completionRecovery)completionRecovery.message=displayError(error);
      message(displayError(error),true);return {status:'recovery_pending'};
    }finally{
      // Release is still required: completing/recovering cannot capture again.
      pending=false;await refresh();
    }
  }
  const completionKey=code=>code==='Space';
  function accepts(code) {
    return code==='Space' && ['ready_process','ready_start'].includes(current?.stage);
  }
  function press(code) {
    if(code==='Space' && completionRecovery){void resumeCompletion();return;}
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
    if(e.code==='Space' && (completionRecovery || ['awaiting_completion','awaiting_manual_completion'].includes(current?.stage))) {
      if(e.repeat || held.has(e.code)){e.preventDefault();return;}
      keydown(e,true);
    }
  });
  $('automaticWorkflow')?.addEventListener('click',()=>{navigateSection('process');render();});
  next.addEventListener('click',()=>void (completionRecovery ? resumeCompletion() : advance()));done.addEventListener('click',()=>void finish(true));reset.addEventListener('click',()=>void finish(false));
  async function nativeComplete(snapshot) {
    // A native completion edge is pinned to its cycle/revision. It cannot call
    // advance(), even if polling or a delayed callback sees a different stage.
    if(completionRecovery && !document.hidden)return resumeCompletion();
    if(state.activeView!=='lasercad' || document.hidden || !snapshot || !['awaiting_completion','awaiting_manual_completion'].includes(snapshot.stage))return {status:'ignored'};
    held.delete('Space');
    return await finish(true,{confirmedBySpace:true,expectedSnapshot:snapshot}) || {status:'blocked'};
  }
  window.SpaceCycle={advance,keydown,decorate,nativeComplete,nativePress:(code)=>{if(code==='Space' && (completionRecovery || state.activeView==='lasercad') && !document.hidden){held.delete('Space');press(code);}}};
  setInterval(()=>void refresh(),750);void refresh();
})();
