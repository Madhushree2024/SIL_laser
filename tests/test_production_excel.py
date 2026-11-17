import csv
import json
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from test_daily_production import fixture_record, rows
from test_multi_sheath import workspace
from app.export.daily_production import record_daily_production
from app.export.production_excel import write_daily_workbook

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

def sheet(path, number):
    with ZipFile(path) as archive:
        return ET.fromstring(archive.read(f'xl/worksheets/sheet{number}.xml'))

def value(xml, ref):
    c=xml.find(f'.//s:c[@r="{ref}"]',NS)
    if c is None:return None
    if c.get('t')=='inlineStr':return c.find('s:is/s:t',NS).text
    v=c.find('s:v',NS)
    return v.text if v is not None else None

def test_excel_is_saved_automatically_with_readable_typed_values_and_unique_retry(tmp_path):
    record=fixture_record(tmp_path)
    csv_path=record_daily_production(tmp_path/'production',**record)
    original=csv_path.read_bytes();path=csv_path.with_suffix('.xlsx')
    assert path.is_file()
    log=sheet(path,2);summary=sheet(path,1);measure=sheet(path,3);totals=sheet(path,4)
    assert value(log,'A6')=='1' and value(log,'E6')=='2.0'
    assert value(log,'F6')=='0.75' and value(log,'G6')=='0.25'
    assert value(log,'I6')=='7 inch, café × 2'
    assert value(log,'J6')=='Spaced side packing'
    assert float(value(measure,'C6'))==263.123456789
    assert float(value(measure,'D6'))==262
    assert value(totals,'A6')=='7 inch, café' and value(totals,'B6')=='2'
    assert value(summary,'B7')=='1' and value(summary,'B9')=='2.0'
    assert log.find('s:sheetViews/s:sheetView/s:pane',NS).get('ySplit')=='5'
    assert log.find('s:autoFilter',NS).get('ref')=='A5:L6'
    record_daily_production(tmp_path/'production',**record)
    assert csv_path.read_bytes()==original
    assert value(sheet(path,1),'B7')=='1'


def test_excel_uses_area_weighted_utilization_and_keeps_unknown_measurements_blank(tmp_path):
    one=fixture_record(tmp_path,'one');two=fixture_record(tmp_path,'two')
    one['nesting'].update(sheath_area_mm2=100,used_area_mm2=50,utilization=.5,waste=.5)
    two['nesting'].update(sheath_area_mm2=300,used_area_mm2=240,utilization=.8,waste=.2)
    path=record_daily_production(tmp_path/'production',**one)
    record_daily_production(tmp_path/'production',**two)
    summary=sheet(path.with_suffix('.xlsx'),1)
    assert float(value(summary,'B13'))==pytest.approx(.725)
    assert float(value(summary,'B14'))==pytest.approx(.275)
    assert value(sheet(path.with_suffix('.xlsx'),3),'E6') is None
    assert float(value(summary,'B8'))==2
    assert value(sheet(path.with_suffix('.xlsx'),4),'B6')=='4'


def test_excel_failure_does_not_fail_or_duplicate_the_saved_production_record(tmp_path,monkeypatch,caplog):
    import app.export.production_excel as excel
    record=fixture_record(tmp_path)
    original=excel.write_daily_workbook
    monkeypatch.setattr(excel,'write_daily_workbook',lambda *a:(_ for _ in ()).throw(PermissionError('Workbook open in Excel')))
    csv_path=record_daily_production(tmp_path/'production',**record)
    assert len(rows(csv_path))==1 and not csv_path.with_suffix('.xlsx').exists()
    assert 'CSV is saved' in caplog.text
    monkeypatch.setattr(excel,'write_daily_workbook',original)
    assert write_daily_workbook(csv_path).is_file()
    assert len(rows(csv_path))==1


def test_excel_treats_user_template_names_as_literal_text(tmp_path):
    record=fixture_record(tmp_path)
    record['nesting']['placements']=[{'template_id':'t','template_name':'=HYPERLINK("evil","click")'}]
    path=record_daily_production(tmp_path/'production',**record).with_suffix('.xlsx')
    log=sheet(path,2);cell=log.find('.//s:c[@r="I6"]',NS)
    assert cell.get('t')=='inlineStr' and cell.find('s:f',NS) is None
    assert value(log,'I6').startswith('=HYPERLINK')


def test_excel_missing_timing_and_area_do_not_become_zero(tmp_path):
    record=fixture_record(tmp_path);record['events']=[]
    record['nesting'].pop('sheath_area_mm2');record['nesting'].pop('used_area_mm2')
    path=record_daily_production(tmp_path/'production',**record).with_suffix('.xlsx')
    summary=sheet(path,1)
    for ref in ['B11','B12','B13','B14','B15','B16']:assert value(summary,ref) is None


def test_excel_rejects_damaged_csv_and_preserves_existing_workbook(tmp_path):
    record=fixture_record(tmp_path);csv_path=record_daily_production(tmp_path/'production',**record)
    before=csv_path.with_suffix('.xlsx').read_bytes()
    csv_path.write_text('wrong,header\n1,2\n',encoding='utf-8')
    with pytest.raises(ValueError,match='header'):write_daily_workbook(csv_path)
    assert csv_path.with_suffix('.xlsx').read_bytes()==before


def test_excel_download_and_listing_use_existing_records_only(workspace):
    client,_,root,_=workspace
    csv_path=record_daily_production(root/'data/production',**fixture_record(root/'excel-fixture'))
    before=csv_path.read_bytes()
    reports=client.get('/api/projects').json()['production_reports']
    assert reports[0]['excel_url']=='/api/production/2026-10-15/excel'
    response=client.get(reports[0]['excel_url'])
    assert response.status_code==200
    assert response.headers['content-type'].startswith('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    assert response.content[:2]==b'PK' and csv_path.read_bytes()==before
    assert len(rows(csv_path))==1
    assert client.get('/api/production/2026-10-14/excel').status_code==404
    assert client.get('/api/production/invalid/excel').status_code==400


def test_excel_download_failure_reports_actionable_error_without_changing_csv(workspace,monkeypatch):
    import app.export.production_excel as excel
    client,_,root,_=workspace
    csv_path=record_daily_production(root/'data/production',**fixture_record(root/'excel-fixture'))
    before=csv_path.read_bytes()
    monkeypatch.setattr(excel,'write_daily_workbook',lambda *a:(_ for _ in ()).throw(PermissionError('Workbook open')))
    response=client.get('/api/production/2026-10-15/excel')
    assert response.status_code==400 and 'Close the workbook' in response.json()['detail']
    assert csv_path.read_bytes()==before
