"""Readable Excel snapshots of the existing daily production CSV ledger.

The styled template is generated at development time. Runtime filling uses only
Python's standard library, so operators need no spreadsheet package installed.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import csv
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import tempfile
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
ET.register_namespace('', _NS)
_TEMPLATE = Path(__file__).with_name('templates') / 'daily_production.xlsx'
MODES = {'common_line': 'Shared cut', 'shared_side': 'Spaced side packing', 'accurate': 'Dense layout',
         'fast': 'Fast layout', 'maxrects': 'Rectangle packing'}


def _tag(name):
    return '{' + _NS + '}' + name


def _number(value):
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError('Invalid numeric value in production record')
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError('Invalid measurement or count in production record')
    return number


def _list(value):
    data = json.loads(value) if value else []
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise ValueError('Invalid template or sheath details in production CSV')
    return data


def _name(text):
    return re.sub(r'(\d+(?:\.\d+)?)\s*["\u2033]', r'\1 inch', str(text or 'Unnamed template'))


def _date_number(value):
    # Excel dates have no timezone; source timestamps already specify India time.
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError('Export timestamp has no timezone')
    from .daily_production import PRODUCTION_TIMEZONE
    local = stamp.astimezone(PRODUCTION_TIMEZONE).replace(tzinfo=None)
    return (local - datetime(1899, 12, 30)).total_seconds() / 86400


def _cell(sheet, ref):
    data = sheet.find(_tag('sheetData'))
    rownum = int(re.search(r'\d+', ref).group())
    row = next((r for r in data if int(r.get('r')) == rownum), None)
    if row is None:
        row = ET.SubElement(data, _tag('row'), {'r': str(rownum)})
    cell = next((c for c in row if c.get('r') == ref), None)
    return cell if cell is not None else ET.SubElement(row, _tag('c'), {'r': ref})


def _put(sheet, ref, value, formula=None):
    cell = _cell(sheet, ref)
    for child in list(cell):
        cell.remove(child)
    cell.attrib.pop('t', None)
    if formula:
        ET.SubElement(cell, _tag('f')).text = formula
    if value is None:
        if formula:
            cell.set('t', 'str')
            ET.SubElement(cell, _tag('v')).text = ''
    elif isinstance(value, (float, int)):
        ET.SubElement(cell, _tag('v')).text = str(value)
    elif formula:
        cell.set('t', 'str')
        ET.SubElement(cell, _tag('v')).text = str(value)
    else:
        # Literal text, including names beginning with =, is never a formula.
        cell.set('t', 'inlineStr')
        node = ET.SubElement(ET.SubElement(cell, _tag('is')), _tag('t'))
        node.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        node.text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))


def _rows(sheet, records, columns, *, wrap_column=None):
    data = sheet.find(_tag('sheetData'))
    prototypes = [deepcopy(next(r for r in data if r.get('r') == str(i))) for i in [6, 7]]
    for row in list(data):
        if int(row.get('r')) >= 6:
            data.remove(row)
    for index, values in enumerate(records or [[None] * columns], 6):
        row = deepcopy(prototypes[(index - 6) % 2])
        row.set('r', str(index))
        if wrap_column is not None:
            lines = max(1, math.ceil(len(str(values[wrap_column] or '')) / 40))
            row.set('ht', str(max(32, lines * 16 + 8)))
            row.set('customHeight', '1')
        for col, cell in enumerate(row):
            cell.set('r', f'{chr(65 + col)}{index}')
        data.append(row)
        for col, value in enumerate(values):
            _put(sheet, f'{chr(65 + col)}{index}', value)
    last = max(6, len(records) + 5)
    end = chr(64 + columns)
    dimension = sheet.find(_tag('dimension'))
    if dimension is None:
        dimension = ET.Element(_tag('dimension'))
        sheet.insert(1 if sheet.find(_tag('sheetPr')) is not None else 0, dimension)
    dimension.set('ref', f'A1:{end}{last}')
    auto = sheet.find(_tag('autoFilter'))
    if auto is None:
        auto = ET.Element(_tag('autoFilter'))
        # autoFilter follows sheetData and precedes page/print/extension settings.
        sheet.insert(list(sheet).index(data) + 1, auto)
    auto.set('ref', f'A5:{end}{last}')


def write_daily_workbook(source: Path) -> Path:
    """Caller holds the daily-ledger lock; replace the derived workbook atomically."""
    source = Path(source)
    with source.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        from .daily_production import FIELDS
        if reader.fieldnames != FIELDS:
            raise ValueError('Daily production CSV has an unexpected header')
        records = list(reader)
    if any(set(row) != set(FIELDS) or any(v is None for v in row.values()) for row in records):
        raise ValueError('Daily production CSV contains a damaged row')
    records.sort(key=lambda row: row['exported_at_local'])
    day = datetime.strptime(source.stem.removeprefix('production_'), '%Y-%m-%d')
    if any(row['production_date'] != day.date().isoformat() or row['status'] != 'exported' for row in records):
        raise ValueError('Daily production CSV has inconsistent dates or status')
    jobs, measurements, pieces = [], [], Counter()
    for index, row in enumerate(records, 1):
        templates = _list(row['template_counts_json'])
        names = []
        for item in templates:
            count = _number(item.get('count'))
            if count is None or not count.is_integer():
                raise ValueError('Template count is missing or invalid')
            name = _name(item.get('name'))
            pieces[(str(item.get('template_id', '')), name)] += int(count)
            names.append(f'{name} × {int(count)}')
        used = _number(row['utilization_percent']); waste = _number(row['waste_percent'])
        material = _number(row['material_area_mm2']); occupied = _number(row['used_area_mm2'])
        jobs.append([index, _date_number(row['exported_at_local']), _number(row['sheath_count']),
                     _number(row['requested_piece_count']), _number(row['exported_piece_count']),
                     None if used is None else used / 100, None if waste is None else waste / 100,
                     _number(row['cycle_seconds']), '; '.join(names), MODES.get(row['strategy'], row['strategy'].replace('_', ' ').title()),
                     None if material is None else material / 100, None if occupied is None else occupied / 100])
        for number, item in enumerate(_list(row['sheath_measurements_json']), 1):
            m = item.get('measurements', {})
            if not isinstance(m, dict):
                raise ValueError('Invalid sheath measurements')
            area = _number(m.get('area_mm2'))
            measurements.append([index, f'Sheath {number}', _number(m.get('aligned_width_mm')),
                                 _number(m.get('aligned_height_mm')), None if area is None else area / 100])
    totals = [[name, count] for (_, name), count in sorted(pieces.items(), key=lambda item: item[0][1].casefold())]
    with ZipFile(_TEMPLATE) as template:
        files = {name: template.read(name) for name in template.namelist()}
    sheets = [ET.fromstring(files[f'xl/worksheets/sheet{i}.xml']) for i in range(1, 5)]
    for index, sheet in enumerate(sheets):
        if index == 0:
            _put(sheet, 'B3', day.strftime('%d %b %Y') + ' · India time')
        else:
            _put(sheet, 'A3', day.strftime('%d %b %Y') + ' · India time')
            _put(sheet, 'B3', None)
    _rows(sheets[1], jobs, 12, wrap_column=8)
    _rows(sheets[2], measurements, 5)
    _rows(sheets[3], totals, 2, wrap_column=0)
    summary = sheets[0];last = max(6, len(jobs) + 5)
    _put(summary, 'B6', (day - datetime(1899, 12, 30)).days)
    _put(summary, 'B22', source.name)
    # Missing source values remain blank instead of silently contributing zero.
    for target, column in [(7,'A'), (8,'C'), (9,'E'), (10,'D'), (11,'K'), (12,'L')]:
        col = ord(column) - 65;values = [job[col] for job in jobs]
        complete = all(value is not None for value in values)
        value = (len(jobs) if target == 7 else sum(values)) if complete else None
        ref = f"'Production log'!{column}6:{column}{last}"
        _put(summary, f'B{target}', value, f'COUNTA({ref})' if target == 7 else f'IF(COUNT({ref})<>$B$7,"",SUM({ref}))')
    material = sum(job[10] for job in jobs) if all(job[10] is not None for job in jobs) else None
    occupied = sum(job[11] for job in jobs) if all(job[11] is not None for job in jobs) else None
    ratio = occupied / material if material and occupied is not None else None
    _put(summary, 'B13', ratio, 'IF(OR(B11="",B12="",B11=0),"",B12/B11)')
    _put(summary, 'B14', None if ratio is None else 1 - ratio, 'IF(B13="","",1-B13)')
    times = [job[7] for job in jobs if job[7] is not None]
    ref = f"'Production log'!H6:H{last}"
    _put(summary, 'B15', sum(times)/len(times) if times else None, f'IF(COUNT({ref})=0,"",AVERAGE({ref}))')
    _put(summary, 'B16', sum(times)/60 if times else None, f'IF(COUNT({ref})=0,"",SUM({ref})/60)')
    for i, sheet in enumerate(sheets, 1):
        files[f'xl/worksheets/sheet{i}.xml'] = ET.tostring(sheet, encoding='utf-8', xml_declaration=True)
    workbook = ET.fromstring(files['xl/workbook.xml'])
    calc = workbook.find(_tag('calcPr'))
    if calc is None:
        calc = ET.SubElement(workbook, _tag('calcPr'))
    calc.set('fullCalcOnLoad', '1');calc.set('forceFullCalc', '1')
    files['xl/workbook.xml'] = ET.tostring(workbook, encoding='utf-8', xml_declaration=True)
    target = source.with_suffix('.xlsx');temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.'+target.name+'.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            with ZipFile(stream, 'w', compression=ZIP_DEFLATED) as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target
