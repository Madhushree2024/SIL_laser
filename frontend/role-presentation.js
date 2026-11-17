/* Role-specific presentation only. Backend permissions remain authoritative. */
(() => {
  const friendly = {
    permission:'Your account is read-only. Ask an Admin or Manager to make changes.',
    session:'Your session has ended. Please sign in again.',
    connection:'The app is not responding. Reopen it and try again.',
    missing:'The requested file or information was not found.',
    camera:'The camera is unavailable. Ask an Admin or Manager to check it.',
    report:'The report could not be opened. Try Refresh or ask an Admin for help.',
    file:'The file could not be opened. Try again or ask an Admin for help.',
    laser:'LaserCAD is unavailable. Ask an Admin or Manager for help.',
    passwordMatch:'The passwords do not match.',
    passwordCurrent:'Your current password is incorrect.',
    passwordLength:'Use a password with at least 12 characters.',
    password:'The password could not be changed. Check it and try again.',
    generic:'This information could not be loaded. Try again or ask an Admin for help.'
  };
  const known = new Set(Object.values(friendly));
  const logged = new Set();
  function error(value, {role, path='', status}={}) {
    const text=typeof value==='string' ? value : value?.message || String(value ?? '');
    if(role!=='viewer' || !text)return text;
    if(value?.userMessage)return value.userMessage;
    if(known.has(text))return text;
    const code=status ?? value?.status;
    let result;
    if(code===401 || /session.*(ended|expired)|sign in again/i.test(text))result=friendly.session;
    else if(code===403 || /permission|read.only|not authorized|access denied/i.test(text))result=friendly.permission;
    else if(/password/i.test(text)) {
      result=/not match|mismatch/i.test(text)?friendly.passwordMatch
        :/current.*(incorrect|invalid|wrong)/i.test(text)?friendly.passwordCurrent
        :/12|at least|too short/i.test(text)?friendly.passwordLength:friendly.password;
    }
    else if(code===404)result=friendly.missing;
    else if(/network|failed to fetch|cannot reach|offline|connection (lost|refused)|server.*unavailable/i.test(text))result=friendly.connection;
    else if(/camera|capture.*frame|frame.*capture|webcam/i.test(text+' '+path))result=friendly.camera;
    else if(/production|report|csv|spreadsheet/i.test(text+' '+path))result=friendly.report;
    else if(/lasercad|controller|sdk/i.test(text+' '+path))result=friendly.laser;
    else if(/download|file|project|dxf/i.test(text+' '+path))result=friendly.file;
    else result=friendly.generic;
    if(!logged.has(text)) {
      if(logged.size>=50)logged.clear();logged.add(text);
      console.error('[Read-only error details]', {path,status:code,detail:text});
    }
    return result;
  }
  function apply(role) {
    if(document.body)document.body.dataset.userRole=role || '';
  }
  window.RolePresentation={error,apply};
})();
