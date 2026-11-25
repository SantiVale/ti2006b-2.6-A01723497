"""Modelo dimensional local. Ejecutar desde cualquier directorio con Python 3.11+."""
from pathlib import Path
import calendar, datetime, hashlib, sqlite3
import pandas as pd
import openpyxl

ROOT = Path(__file__).resolve().parents[1]
RAW, OUT = ROOT / 'data/raw', ROOT / 'data/processed'

def numeric(value):
    # Formatos ambiguos con separadores se conservan como nulos para revisión.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(value.strip()) if isinstance(value, str) else None
    except ValueError:
        return None

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    paths = sorted(RAW.glob('*.xlsx'))
    books = {p.name: openpyxl.load_workbook(p, data_only=True) for p in paths}
    bit = books['Bitacora descargas 2026.xlsx']
    rep = books['ReportingFactory Ene26 - Oct26.xlsx']
    plants = sorted(set(bit.sheetnames) | (set(rep.sheetnames) - {'TOTAL'}))
    dim_plant = pd.DataFrame([{'planta_id': i+1, 'planta': p, 'en_bitacora': p in bit.sheetnames,
                              'en_reporting': p in rep.sheetnames} for i,p in enumerate(plants)])
    ids = dict(zip(dim_plant.planta, dim_plant.planta_id))
    dates = pd.date_range('2026-01-01', '2026-10-31')
    dim_date = pd.DataFrame({'fecha_id': dates.strftime('%Y%m%d').astype(int), 'fecha': dates.strftime('%Y-%m-%d'),
                             'anio': dates.year, 'mes': dates.month, 'dia': dates.day,
                             'fin_semana': dates.dayofweek >= 5})
    daily, totals, reporting, inventory = [], [], [], []
    for name, book in books.items():
        for sheet in book:
            inventory.append({'archivo': name, 'hoja': sheet.title, 'filas': sheet.max_row, 'columnas': sheet.max_column})
    for s in bit:
        # Descubrir meses y columnas desde encabezados observados, no desde nombres supuestos.
        for row in s.iter_rows():
            for cell in row:
                if not isinstance(cell.value, datetime.datetime) or cell.value.year != 2026 or cell.value.month > 10:
                    continue
                col, month = cell.column, cell.value.month
                if 'FLUJO' not in str(s.cell(cell.row+1,col).value):
                    continue
                if 'm3' not in str(s.cell(cell.row+4,col+1).value):
                    raise ValueError(f'Unidad no reconocida: {s.title} {cell.coordinate}')
                for stream, offset in [('PTAR',0), ('Riego',2)]:
                    c = col+offset
                    for day in range(1,calendar.monthrange(2026,month)[1]+1):
                        r=cell.row+4+day
                        reading, volume = s.cell(r,c), s.cell(r,c+1)
                        daily.append({'planta_id':ids[s.title], 'fecha_id':20260000+month*100+day,
                                      'corriente':stream, 'lectura_m3':numeric(reading.value),
                                      'volumen_m3':numeric(volume.value), 'valor_original':str(volume.value),
                                      'archivo':'Bitacora descargas 2026.xlsx','hoja':s.title,
                                      'celda_lectura':reading.coordinate,'celda_volumen':volume.coordinate,
                                      'unidad_origen':'m3','origen_valor':'cache Excel'})
                    totals.append({'planta_id':ids[s.title], 'mes':month, 'corriente':stream,
                                   'total_hoja_L':(numeric(s.cell(52,c+1).value)*1000 if numeric(s.cell(52,c+1).value) is not None else None),
                                   'celda_total':s.cell(52,c+1).coordinate})
    for s in rep:
        headers=[c.value for c in s[1]]
        if not all(k in headers for k in ['KOF DIM_TIME[Month]','KOF DIM_TIME[Año]']):
            raise ValueError(f'Esquema Reporting desconocido: {s.title}')
        for r,row in enumerate(s.iter_rows(min_row=2),2):
            vals=dict(zip(headers,[c.value for c in row]))
            if vals['KOF DIM_TIME[Año]']!=2026 or not 1<=vals['KOF DIM_TIME[Month]']<=10: continue
            reporting.append({'planta_id':ids.get(s.title), 'hoja':s.title,'mes':int(vals['KOF DIM_TIME[Month]']),
                              'reporting_L':numeric(row[-1].value),'celda':row[-1].coordinate,
                              'es_total':s.title=='TOTAL','unidad':'L (alcance proporcionado)'})
    daily=pd.DataFrame(daily); daily['volumen_L']=daily.volumen_m3*1000
    daily=daily.merge(dim_date[['fecha_id','mes']],on='fecha_id',validate='many_to_one')
    monthly=daily.groupby(['planta_id','mes','corriente']).agg(volumen_L=('volumen_L',lambda x:x.sum(min_count=1)),
                            dias_validos=('volumen_L','count'),dias_esperados=('fecha_id','size')).reset_index()
    monthly=monthly.merge(pd.DataFrame(totals),on=['planta_id','mes','corriente'],validate='one_to_one')
    monthly['delta_total_interno_L']=monthly.volumen_L-monthly.total_hoja_L
    reporting=pd.DataFrame(reporting)
    reconciliation=monthly.pivot(index=['planta_id','mes'],columns='corriente',values='volumen_L').reset_index()
    reconciliation.columns.name=None
    reconciliation=reconciliation.merge(reporting[~reporting.es_total][['planta_id','mes','reporting_L','celda']],on=['planta_id','mes'],how='outer',validate='one_to_one')
    reconciliation['PTAR_mas_Riego']=reconciliation.PTAR+reconciliation.Riego
    for base in ['PTAR','Riego','PTAR_mas_Riego']:
        reconciliation[f'diferencia_{base}_L']=reconciliation[base]-reconciliation.reporting_L
    sources=pd.DataFrame([{'archivo':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths])
    tables={'dim_planta':dim_plant,'dim_fecha':dim_date,'fact_lectura_diaria':daily,
            'fact_bitacora_mensual':monthly,'fact_reporting_mensual':reporting,
            'fact_conciliacion':reconciliation,'inventario_hojas':pd.DataFrame(inventory),'fuentes':sources}
    with sqlite3.connect(OUT/'descargas.sqlite') as conn:
        for name,df in tables.items():
            df.to_csv(OUT/f'{name}.csv',index=False)
            df.to_sql(name,conn,if_exists='replace',index=False)
    print(f'Modelo generado: {len(plants)} plantas, {len(daily)} lecturas por corriente, {len(reconciliation)} planta-mes.')
    print(OUT/'descargas.sqlite')

if __name__ == '__main__':
    main()
