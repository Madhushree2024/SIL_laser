/* Read-only readiness and sequence inspection. No motion or Start requests. */
(() => {
  const controls=[$('machineWorkflowCheck'),$('machineWorkflowPreview')].filter(Boolean);
  async function preview(){
    controls.forEach(button=>button.disabled=true);
    try {
      const result=await api('/api/laser-control/machine-workflow',{cache:'no-store'});
      $('machineWorkflowReadiness').textContent=result.message;
      const steps=$('machineWorkflowSteps');steps.replaceChildren();
      for(const step of result.sequence){const row=document.createElement('li');row.textContent=step;steps.append(row);}
      steps.hidden=false;
      message((result.enabled?'Automatic machine cycle requested. ':'Automatic machine cycle disabled. ')+result.message,!result.controller_feedback_available);
    } catch(error){message(displayError(error),true);$('machineWorkflowReadiness').textContent=displayError(error);}
    finally {controls.forEach(button=>button.disabled=false);}
  }
  controls.forEach(button=>button.onclick=preview);
})();
