/* Measured machine positions and explicit derivation of the working layout. */
(() => {
  let dirty=false,pending=false,lastLayout='',latest=null;
  const panel=$('machinePlacementPanel');if(!panel)return;
  const status=$('machinePlacementStatus'),rows=$('machinePlacementRows');
  const save=$('saveMachinePlacement'),derive=$('useMachinePositionsAsLayout');
  const buttons=[save,derive].filter(Boolean);
  function controls(){buttons.forEach(b=>b.disabled=pending||!hasPermission('configure')||latest?.workspace_layout?.markers?.length!==4);}
  function sameIds(a,b){return a?.dictionary===b?.dictionary && JSON.stringify(a?.markers?.map(m=>m.marker_id).sort())===JSON.stringify(b?.markers?.map(m=>m.marker_id).sort());}
  function render(data){
    latest=data;const layout=data.workspace_layout,key=JSON.stringify(layout||null);
    const placement=data.machine_placement||{ready:false,message:'Machine placement is not measured. Save marker positions, then survey their actual machine X/Y.'};
    status.textContent=placement.ready
      ? `Measured placement saved. Maximum fit residual ${placement.max_fit_error_mm.toFixed(3)} mm. DXF (0,0) stays local; LaserCAD keeps the photographed bed position. Verify controller positioning mode before cutting.`
      : placement.message;
    if(!dirty&&!pending){
      const points=new Map((placement.machine_markers||[]).map(m=>[m.marker_id,m]));rows.replaceChildren();
      for(const marker of layout?.markers||[]){
        const row=document.createElement('div');row.className='bed-marker';row.dataset.machineMarker=String(marker.marker_id);
        const label=document.createElement('span');label.textContent=`ID ${marker.marker_id}`;row.append(label);
        for(const axis of ['x','y']){
          const field=document.createElement('label');field.textContent=`Machine ${axis.toUpperCase()} mm`;
          const input=document.createElement('input');input.type='number';input.step='any';input.required=true;input.dataset.machineAxis=axis;
          input.setAttribute('aria-label',`ID ${marker.marker_id} machine ${axis.toUpperCase()} millimetres`);
          input.value=points.has(marker.marker_id)?String(points.get(marker.marker_id)[axis+'_mm']):'';
          input.disabled=!hasPermission('configure');field.append(input);row.append(field);
        }
        rows.append(row);
      }
      lastLayout=key;
    }else if(dirty&&key!==lastLayout){
      if(sameIds(JSON.parse(lastLayout||'null'),layout)){
        lastLayout=key;status.textContent='Working coordinates updated. Your unsaved machine readings are retained; saving will validate them against the latest layout.';
      }else status.textContent='Working marker IDs or dictionary changed. Reload the saved marker layout before entering machine positions.';
    }
    controls();
  }
  function readings(){
    const layout=latest?.workspace_layout;
    if(!layout||JSON.stringify(layout)!==lastLayout)throw Error('Reload the saved marker layout before entering machine positions.');
    const markers=[...rows.querySelectorAll('[data-machine-marker]')].map(row=>{
      const read=axis=>{const text=row.querySelector(`[data-machine-axis="${axis}"]`).value.trim();if(!text||!Number.isFinite(Number(text)))throw Error('Enter all eight measured X/Y values. Blank fields are not zero.');return Number(text);};
      return {marker_id:Number(row.dataset.machineMarker),x_mm:read('x'),y_mm:read('y')};
    });
    return {dictionary:layout.dictionary,markers};
  }
  rows.addEventListener('input',()=>{dirty=true;status.textContent='Unsaved machine positions. No placement or laser command has been changed.';});
  async function submit(replaceLayout){
    if(pending||!hasPermission('configure'))return;
    try{
      const body=readings();pending=true;controls();
      const endpoint=replaceLayout?'/api/calibration/layout-from-machine':'/api/calibration/machine-placement';
      const result=await api(endpoint,{method:'POST',body:JSON.stringify(body)});
      if(replaceLayout){
        state.workspaceLayoutDirty=false;state.workspaceDimensionsDirty=false;
        if(result.changed&&typeof clearWorkspaceGeometry==='function')clearWorkspaceGeometry();
      }
      dirty=false;pending=false;renderCalibrationStatus(result.calibration);
      message(replaceLayout?'Surveyed working layout and machine placement saved together. Capture all four markers, detect and nest again.':'Measured machine placement saved. Capture and nest again, then verify the mapped drawing and physical job positioning before cutting.');
    }catch(error){message(displayError(error),true);status.textContent=displayError(error);}
    finally{pending=false;controls();}
  }
  save.onclick=()=>submit(false);if(derive)derive.onclick=()=>submit(true);
  const original=renderCalibrationStatus;renderCalibrationStatus=function(data){original(data);render(data);};
  if(state.savedCalibration)render(state.savedCalibration);
})();
