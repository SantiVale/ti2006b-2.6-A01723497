"""Construye Gold local con reglas explícitas; ninguna hipótesis modifica la fuente."""
from pathlib import Path
import json,sqlite3
import numpy as np
import pandas as pd
from modelar import main as modelar
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data/processed'

def main():
    modelar()
    rules=json.loads((ROOT/'config.json').read_text())
    with sqlite3.connect(OUT/'descargas.sqlite') as conn:
        read=lambda t:pd.read_sql_query(f'SELECT * FROM {t}',conn)
        d=read('fact_lectura_diaria');c=read('fact_conciliacion');plants=read('dim_planta');monthly=read('fact_bitacora_mensual');rep=read('fact_reporting_mensual')
        events=[]
        for (plant,stream),g in d.groupby(['planta_id','corriente']):
            g=g.sort_values('fecha_id').copy();pos=g.loc[g.volumen_m3>0,'volumen_m3'];med=pos.median();mad=(pos-med).abs().median()
            # Inactive irrigation is retained, not classified as missing discharge.
            if stream=='Riego' and (len(pos)==0):continue
            lengths=g.groupby(g.volumen_m3.fillna(-999999).ne(g.volumen_m3.shift().fillna(-999999)).cumsum()).fecha_id.transform('size')
            for i,row in g.iterrows():
                v=row.volumen_m3;types=[]
                if pd.isna(v):types.append('hueco')
                elif v<0:types.append('negativo')
                elif v==0:types.append('cero')
                else:
                    if (mad>0 and abs(.67448975*(v-med)/mad)>rules['robust_z']) or (med>0 and v>10*med):types.append('outlier')
                    if lengths.loc[i]>=rules['racha_minima']:types.append('repetido')
                for typ in types:events.append({**row.to_dict(),'tipo':typ,'mediana_m3':med,'mad_m3':mad})
        alerts=pd.DataFrame(events,columns=list(d.columns)+['tipo','mediana_m3','mad_m3'])
        for base in ['PTAR','Riego','PTAR_mas_Riego']:
            c[f'brecha_{base}_L']=abs(c[base]-c.reporting_L)
        ratio=c.PTAR/c.reporting_L.replace(0,np.nan);c['factor_candidato']=1.
        for factor in [10,100,1000,.1,.01,.001]:c.loc[ratio.between(factor*.8,factor*1.2),'factor_candidato']=factor
        c['reporting_hipotesis_L']=c.reporting_L*c.factor_candidato
        c['brecha_original_L']=c.brecha_PTAR_L
        c['brecha_residual_L']=abs(c.PTAR-c.reporting_hipotesis_L)
        c['reduccion_hipotetica_L']=c.brecha_original_L-c.brecha_residual_L
        c['pct_residual']=100*c.brecha_residual_L/c.reporting_hipotesis_L.abs().replace(0,np.nan)
        c['estado_hipotesis']='Sin aprobación; fuente intacta'
        coverage=monthly[monthly.corriente.eq('PTAR')][['planta_id','mes','dias_validos','dias_esperados','celda_total','total_hoja_L']]
        c=c.merge(coverage,on=['planta_id','mes'],validate='one_to_one')
        c['semaforo_mes']=np.where(c.factor_candidato.ne(1)|(c.pct_residual.gt(rules['rojo_pct_mes'])&c.brecha_residual_L.gt(rules['rojo_L_mes'])),'Rojo',np.where(c.pct_residual.lt(rules['verde_pct'])&c.brecha_residual_L.lt(rules['verde_L_acumulado'])&c.dias_validos.eq(c.dias_esperados),'Verde','Amarillo'))
        rank=c.groupby('planta_id').agg(reporting_hipotesis_L=('reporting_hipotesis_L','sum'),brecha_original_L=('brecha_original_L','sum'),brecha_residual_L=('brecha_residual_L','sum'),meses_escala=('factor_candidato',lambda x:sum(x!=1)),max_pct=('pct_residual','max'),max_brecha_mes_L=('brecha_residual_L','max'),dias_validos=('dias_validos','sum'),dias_esperados=('dias_esperados','sum')).reset_index()
        rank['pct_residual']=100*rank.brecha_residual_L/rank.reporting_hipotesis_L.abs().replace(0,np.nan)
        counts=alerts[alerts.corriente.eq('PTAR')].groupby(['planta_id','tipo']).size().unstack(fill_value=0)
        rank=rank.merge(counts,on='planta_id',how='left').fillna(0)
        for col in ['hueco','negativo','cero','repetido','outlier']:
            if col not in rank:rank[col]=0
        red=rank.meses_escala.gt(0)|(rank.max_pct.gt(rules['rojo_pct_mes'])&rank.max_brecha_mes_L.gt(rules['rojo_L_mes']))|rank.negativo.ge(2)|rank.hueco.ge(rules['huecos_rojo'])
        green=rank.pct_residual.lt(rules['verde_pct'])&rank.brecha_residual_L.lt(rules['verde_L_acumulado'])&rank.dias_validos.eq(rank.dias_esperados)&rank.negativo.eq(0)
        rank['semaforo']=np.where(red,'Rojo',np.where(green,'Verde','Amarillo'))
        rank=rank.merge(plants,on='planta_id').sort_values(['brecha_residual_L'],ascending=False);rank.insert(0,'ranking_brecha',range(1,len(rank)+1))
        control=rep[rep.es_total.eq(1)][['mes','reporting_L']].merge(rep[rep.es_total.eq(0)].groupby('mes').reporting_L.sum().rename('suma_plantas_L'),on='mes');control['delta_L']=control.reporting_L-control.suma_plantas_L
        checks=[('clave_diaria_unica',not d.duplicated(['planta_id','fecha_id','corriente']).any()),('clave_conciliacion_unica',not c.duplicated(['planta_id','mes']).any()),('total_reporting_coincide',control.delta_L.abs().le(1).all()),('sumas_internas_coinciden',monthly.delta_total_interno_L.abs().le(1).all()),('cobertura_plantas',plants.en_bitacora.eq(1).all() and plants.en_reporting.eq(1).all())]
        outputs={'gold_conciliacion':c,'gold_alertas':alerts,'gold_prioridad':rank,'control_reporting':control,'controles_ejecucion':pd.DataFrame(checks,columns=['control','resultado'])}
        for name,df in outputs.items():df.to_csv(OUT/f'{name}.csv',index=False);df.to_sql(name,conn,if_exists='replace',index=False)
        with pd.ExcelWriter(OUT/'auditoria.xlsx',engine='openpyxl') as writer:
            for name,df in outputs.items():
                df.to_excel(writer,sheet_name=name,index=False);ws=writer.sheets[name];ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
        summary={'plantas':len(plants),'planta_mes':len(c),'brecha_original_L':float(c.brecha_original_L.sum()),'brecha_residual_hipotesis_L':float(c.brecha_residual_L.sum()),'semaforo':rank.semaforo.value_counts().to_dict(),'reglas':rules}
        (OUT/'resumen.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False))
        print(json.dumps(summary,indent=2,ensure_ascii=False))
        if not all(bool(x[1]) for x in checks):raise ValueError('Revisar controles_ejecucion: al menos un control falló')
if __name__=='__main__':main()
