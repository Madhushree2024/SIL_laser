"""Classify only explicit LaserCAD dialog text, never a delivered click."""
import re
from time import monotonic, sleep


def classify_message(texts):
    lines=[re.sub(r'\s+',' ',str(text)).strip() for text in texts if str(text).strip()]
    failures=[line for line in lines if re.search(
        r'communicat(?:ing|ion).*(?:unsuccessful|fail(?:ed|ure)?)|\btime[ -]?out\b|'
        r'\b(?:download|transfer|connection)\b.*\b(?:failed|failure|error)\b|'
        r'\bUSB\b.*\b(?:disconnected|not connected)\b',line,re.I)]
    if failures:return {'status':'failed','dialog_text':failures[0],'reason':'LaserCAD reported a transfer/communication error.'}
    successes=[line for line in lines if re.fullmatch(r'Communicating successful\s*[!.！]?',line,re.I)]
    if len(successes)==1:return {'status':'completed','dialog_text':successes[0],'reason':'LaserCAD reported successful communication after this download.'}
    return {'status':'unconfirmed','dialog_text':' | '.join(lines)[:600],
            'reason':'An unexpected LaserCAD dialog appeared. It was left open for review.'}


def is_loading_dialog(dialog):
    # Demonstrated LaserCAD Download Document progress; never a success result.
    title=re.sub(r'[.\u2026]+$', '', str(dialog.get('title','')).strip()).casefold()
    lines=[re.sub(r'\s+',' ',str(t)).strip().rstrip('.\u2026').casefold()
           for t in dialog.get('texts',[]) if str(t).strip()]
    return title=='dealing' and bool(lines) and all(t=='loading the document' for t in lines)


def wait_for_transfer(observe,baseline,timeout=30,clock=monotonic,pause=sleep):
    if not 5<=timeout<=120:raise ValueError('Transfer confirmation timeout must be between 5 and 120 seconds.')
    deadline=clock()+timeout
    while True:
        # The observer has already verified document/process identity and ownership.
        fresh=[d for d in observe() if d['hwnd'] not in baseline and d.get('owned_by_download') and d.get('texts')]
        fresh=[d for d in fresh if not is_loading_dialog(d)]
        if len(fresh)>1:
            return {'status':'unconfirmed','reason':'Multiple new download dialogs appeared; transfer could not be attributed reliably.','dialog_text':''}
        if fresh:
            result=classify_message(fresh[0]['texts'])
            result.update(dialog_id=fresh[0]['hwnd'],confirmation_source='lasercad_download_dialog')
            return result
        if clock()>=deadline:
            return {'status':'unconfirmed','reason':'Timed out waiting for LaserCAD\'s Communicating successful message. Transfer completion was not confirmed.','dialog_text':''}
        pause(.1)
