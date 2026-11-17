/* Passive diagnostics. Event labels do not invoke their named machine actions. */
(() => {
  const base='/api/laser-control/recordings';
  let active=null, busy=false;
  const message=text=>{$('operatorRecordingStatus').textContent=text;};
  const value=id=>$(id).value.trim();
  function downloadLink() {
    const id=$('operatorRecordingSessions').value;
    const link=$('operatorRecordingDownload');
    link.hidden=!id;
    if(id)link.href=base+'/'+encodeURIComponent(id)+'/download';
    else link.removeAttribute('href');
  }
  async function refresh() {
    if(busy || !state.user || state.activeView!=='lasercad' || document.hidden)return;
    busy=true;
    try {
      const result=await api(base,{cache:'no-store'});
      const wasActive=active;
      active=result.active;
      $('operatorRecordingSummary').textContent=active?'Operator recording: ACTIVE':'Operator recording: off';
      $('operatorRecordingSummary').dataset.active=String(Boolean(active));
      const mayOperate=hasPermission('operate');
      $('operatorRecordingStart').disabled=Boolean(active)||!mayOperate;
      $('operatorRecordingStop').disabled=!active||!mayOperate;
      $('operatorRecordingEvent').disabled=!active||!mayOperate;
      $('operatorRecordingOrigin').disabled=Boolean(active);
      $('operatorRecordingFirmware').disabled=Boolean(active);
      const select=$('operatorRecordingSessions'),previous=select.value;
      select.replaceChildren();
      if(!result.sessions.length)select.add(new Option('No recordings yet',''));
      for(const session of result.sessions) {
        select.add(new Option(new Date(session.started_at).toLocaleString()+' · '+session.state,session.id));
      }
      if(result.sessions.some(x=>x.id===previous))select.value=previous;
      else if(active)select.value=active;
      const current=result.sessions.find(x=>x.id===(active||select.value));
      $('operatorRecordingFolder').textContent=current?'Saved in: '+current.folder:'';
      if(!active && wasActive)message('Recording ended. '+(result.sessions.find(x=>x.id===wasActive)?.end_reason||'No machine command was sent.'));
      if(active)message('Recording visible UI changes in the background. Manually record event labels before/after your LaserCAD actions. No machine commands are sent.');
      downloadLink();
    } catch(error) {message('Recording status unavailable: '+displayError(error));}
    finally {busy=false;}
  }
  $('operatorRecordingStart').onclick=async()=>{
    $('operatorRecordingStart').disabled=true;
    try {
      const result=await api(base,{method:'POST',body:JSON.stringify({
        notes:value('operatorRecordingNotes'),origin_mode:value('operatorRecordingOrigin'),
        firmware:value('operatorRecordingFirmware')
      })});
      active=result.id;
      $('operatorRecordingSessions').value='';
      message('Recording started. All machine actions remain in LaserCAD.');
    } catch(error){message(displayError(error));}
    await refresh();
  };
  $('operatorRecordingStop').onclick=async()=>{
    if(!active)return;
    $('operatorRecordingStop').disabled=true;
    try {
      await api(base+'/stop',{method:'POST',body:JSON.stringify({session_id:active})});
      active=null;
      message('Recording saved. This did not stop or operate the machine. Download its logs ZIP below.');
    } catch(error){message(displayError(error));}
    await refresh();
  };
  $('operatorRecordingEvent').onclick=async()=>{
    if(!active)return;
    $('operatorRecordingEvent').disabled=true;
    try {
      const coordinate=id=>value(id)===''?null:Number(value(id));
      const x=coordinate('operatorRecordingX'),y=coordinate('operatorRecordingY');
      if((x!==null&&!Number.isFinite(x))||(y!==null&&!Number.isFinite(y)))throw new Error('Enter finite observed X/Y readings or leave them blank.');
      await api(base+'/'+encodeURIComponent(active)+'/events',{method:'POST',body:JSON.stringify({
        action:value('operatorRecordingAction'),notes:value('operatorRecordingNotes'),x,y
      })});
      message('Operator-reported event saved. No command was sent to the machine.');
    } catch(error){message(displayError(error));}
    $('operatorRecordingEvent').disabled=!active||!hasPermission('operate');
  };
  $('operatorRecordingSessions').onchange=downloadLink;
  $('operatorRecordingPanel').addEventListener('toggle',()=>{if($('operatorRecordingPanel').open)void refresh();});
  const timer=setInterval(refresh,3000);
  window.addEventListener('pagehide',()=>clearInterval(timer));
})();
