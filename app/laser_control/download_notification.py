"""Desktop transfer notification only; never sends LaserCAD or machine commands."""
from .lasercad_transfer_result import classify_message


def success_popup_text(result):
    transfer=result.get('transfer_confirmation') or {}
    if not (result.get('transfer_completed') is True
            and result.get('lasercad_reported_success') is True
            and transfer.get('status')=='completed'
            and transfer.get('confirmation_source')=='lasercad_download_dialog'
            and transfer.get('dialog_id')
            and classify_message([transfer.get('dialog_text','')])['status']=='completed'):
        return None
    return 'Successful!\n\nDXF download completed.\n\n'+str(result.get('message','LaserCAD confirmed Communicating successful! Start remains manual.'))


def notify_download_success(form_getter,result):
    text=success_popup_text(result)
    if text is None:return
    from System import Action
    from System.Windows.Forms import MessageBox, MessageBoxButtons, MessageBoxIcon
    form=form_getter()
    def show():
        if not form.IsDisposed:
            MessageBox.Show(form,text,'Download successful',MessageBoxButtons.OK,MessageBoxIcon.Information)
    # Queue on the desktop UI thread after the download sequence releases its locks.
    form.BeginInvoke(Action(show))
