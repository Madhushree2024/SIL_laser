/* Explicit manual homing only. No keyboard mapping or workflow advancement. */
(() => {
  const button=$('manualDatum');let pending=false;
  button.addEventListener('click',async()=>{
    if(pending || !state.user || !hasPermission('operate') || state.workflowBusy || state.pendingMutations || state.cameraRefreshing)return;
    pending=true;button.dataset.pending='true';button.disabled=true;
    try{
      const cycle=await api('/api/laser-control/space-cycle',{cache:'no-store'});
      if(!['ready_process','interrupted'].includes(cycle.stage))throw new Error('Finish the active workflow before requesting Datum.');
      if(!confirm('Select the configured USB connection and request manual Datum? Confirm the machine is idle and the head can move freely. This does not start or reset the automatic workflow.'))return;
      const result=await api('/api/laser-control/manual-datum',{method:'POST',body:JSON.stringify({request_id:crypto.randomUUID(),operator_confirmed_idle:true})});
      message(result.message);
    }catch(error){message(displayError(error),true);}
    finally{
      pending=false;button.dataset.pending='false';
      button.disabled=!hasPermission('operate') || state.workflowBusy || !!state.pendingMutations;
    }
  });
})();
