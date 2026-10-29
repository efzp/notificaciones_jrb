"""Validacion local de contratos y calendario. No ejecuta DDL ni conecta con Azure."""
from pathlib import Path
from datetime import date,timedelta
import importlib.util,json,random,re
from openpyxl import load_workbook

BASE=Path(__file__).resolve().parent
REF=BASE.parent/'analisis_estructura_sabana'
data=json.loads((REF/'resultado_estructura.json').read_text(encoding='utf-8'))
view=(BASE/'03_vistas.sql').read_text(encoding='utf-8')
fragment=view.split('CREATE OR ALTER VIEW rep.vw_consolidado_sabana',1)[1].split('FROM rep.vw_fila_base b',1)[0]
columns=re.findall(r'AS \[([^\]]+)\]',fragment)
assert columns==data['referencia']['encabezados'], 'Orden/nombres de las 85 columnas'
assert len(columns)==85 and len(set(columns))==85
tables=(BASE/'01_tablas.sql').read_text(encoding='utf-8')
table_names=re.findall(r'CREATE TABLE ([\w.]+)',tables)
assert len(table_names)==len(set(table_names))
assert 'core.consolidado' not in table_names and 'rep.consolidado' not in table_names
catalog=(BASE/'02_catalogos.sql').read_text(encoding='utf-8')
field_rows=re.findall(r"^\(1,'([^']+)',(\d+),'(TEXTO|FECHA|IDENTIFICADOR)'",catalog,re.M)
assert [r[0] for r in field_rows]==data['referencia']['encabezados'][4:54]
assert [int(r[1]) for r in field_rows]==list(range(1,51))
tipo_capacity=int(re.search(r'\btipo_dato\s+varchar\((\d+)\)',tables,re.I).group(1))
tipo_check=re.search(r'CK_campo_tipo CHECK\(tipo_dato IN \(([^)]+)\)\)',tables).group(1)
tipo_values=set(re.findall(r"'([^']+)'",tipo_check)) | {r[2] for r in field_rows}
assert max(map(len,tipo_values))<=tipo_capacity, 'tipo_dato no admite todos los valores del catalogo/CHECK'
assert 'NO APLICA → eps' not in catalog and "N'NO APLICA','eps'" not in catalog
spec=importlib.util.spec_from_file_location('original',r'C:\Users\eduar\Downloads\procesar_reportes_sabana.py')
original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
holidays={date.fromisoformat(d) for d,_ in original.FECHAS_COLOMBIA_2026}
assert set(re.findall(r"\('(2026\d{4})',N'",catalog))=={d.strftime('%Y%m%d') for d in holidays}
first,last=date(2025,1,1),date(2027,12,31)
prefix={};working={};total=0;current=first
while current<=last:
    working[current]=int(current.weekday()<5 and current not in holidays)
    total+=working[current];prefix[current]=total;current+=timedelta(days=1)
def sql_algorithm(a,b):
    if a is None or b is None or a not in prefix or b not in prefix:return None
    return prefix[b]-prefix[a]+(working[a]-working[b] if b<a else 0)
pairs={(date(2026,3,20),date(2026,3,23)),(date(2026,3,23),date(2026,3,20)),(None,first),(first,date(8202,8,21))}
rng=random.Random(20261026)
for _ in range(2000):pairs.add((first+timedelta(days=rng.randrange(len(prefix))),first+timedelta(days=rng.randrange(len(prefix)))))
wb=load_workbook(REF/'reporte_sabana_completo.xlsx',read_only=True,data_only=True)
it=wb['Consolidado'].values;headers=next(it)
out_of_range=0
for row in it:
    r=dict(zip(headers,row));aud=original.parse_fecha(r.get('fecha_audiencia'))
    fij=original.parse_fecha(r.get('fecha_fijacion'));des=original.parse_fecha(r.get('fecha_desfijacion'))
    for a,b in [(aud,fij),(fij,des)]+[(aud,original.parse_fecha(r.get(f'fecha_{kind}_{p}'))) for p in original.PARTES_INTERESADAS for kind in ('envio','notificacion')]:
        if a in prefix and b in prefix:pairs.add((a,b))
    for field,value in r.items():
        if field.startswith('fecha_'):
            d=original.parse_fecha(value)
            if d and d not in prefix:out_of_range+=1
wb.close()
tested=0
for a,b in pairs:
    actual=sql_algorithm(a,b)
    if a is None or b is None or a not in prefix or b not in prefix:
        assert actual is None
    else:
        assert actual==original.dias_habiles(a,b,holidays),(a,b,actual)
    tested+=1
result={
 'fecha':'2026-10-26',
 'contrato_entrada':{'columnas':len(field_rows),'orden_coincide':True,
                     'tipo_dato_capacidad':tipo_capacity,'tipo_dato_longitud_maxima':max(map(len,tipo_values)),
                     'tipos_caben_en_columna':True},
 'contrato_salida':{'columnas':len(columns),'orden_y_nombres_coinciden':True},
 'modelo':{'tablas':len(table_names),'tablas_negocio':sum(n.startswith('core.') for n in table_names),'foreign_keys':tables.count('FOREIGN KEY'),
           'no_materializa_consolidado':True},
 'calendario':{'dias':len(prefix),'festivos_2026':len(holidays),'pares_contrastados_con_script_original':tested,'coinciden':True,
                'fechas_de_referencia_fuera_del_rango_configurado':out_of_range},
 'datos_observados':{'archivos':data['archivos_revisados'],'filas':data['total_filas_actuales'],'llaves_caso':data['casos_unicos_actuales']},
 'motor_sql':{'ejecutado':False,'motivo':'No hay motor SQL local disponible; no se despliega en Azure para validar.','pruebas_preparadas':'05_verificacion_motor.sql'},
}
syntax=BASE/'validacion_sintaxis.json'
if syntax.exists():result['sintaxis_microsoft_scriptdom']=json.loads(syntax.read_text(encoding='utf-8-sig'))
(BASE/'validacion_local.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
