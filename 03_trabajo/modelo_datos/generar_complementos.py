"""Genera catalogos y vistas del contrato observado de 85 columnas. No conecta a SQL."""
from pathlib import Path
import csv, json

BASE=Path(__file__).resolve().parent
SOURCE=BASE.parent/'analisis_estructura_sabana'/'resultado_estructura.json'
observed=json.loads(SOURCE.read_text(encoding='utf-8'))
HEADERS=observed['referencia']['encabezados']
FIELDS=HEADERS[4:54]
PARTS=['paciente','arl','afp','eps','aseguradora','ultimoempleador']
NAMES={'arl':'arl','afp':'afp','eps':'eps','aseguradora':'aseguradora','ultimoempleador':'ultimo_empleador'}
ATTRS=[('documento_notificacion',2,'documento_notificacion'),('estado',4,'estado_reportado'),
       ('fecha_notificacion',8,'fecha_notificacion_reportada'),('fecha_envio',16,'fecha_envio_reportada'),
       ('guia',32,'guia_reportada'),('fecha_recibido',64,'fecha_recibido_reportada')]

def quote(s):return "N'"+s.replace("'","''")+"'"

seed="""/* Catalogo inicial. Calendario de COMPATIBILIDAD con el script recibido:
   fines de semana y SOLO festivos 2026 de su configuracion original.
   Los dias 2025/2027 no incorporan otros festivos: no es calendario legal multianual.
   Para cambiar calendario, parsers o mapping, crear un perfil nuevo; no editar el publicado. */
SET NOCOUNT ON;
SET XACT_ABORT ON;
BEGIN TRY
BEGIN TRANSACTION;
IF EXISTS(SELECT 1 FROM cfg.perfil_entrada) OR EXISTS(SELECT 1 FROM cfg.calendario)
    THROW 51002,N'Catalogos iniciales ya cargados; use una migracion versionada.',1;
INSERT cfg.calendario(id_calendario,nombre,version,fecha_desde,fecha_hasta,descripcion,publicado)
VALUES(1,N'COMPAT_SCRIPT_SABANA_2026',1,'20250101','20271231',
 N'Reproduce los festivos 2026 del script original. Fuera de 2026 solo excluye sabados y domingos. No certifica terminos legales.',0);
DECLARE @festivos TABLE(fecha date PRIMARY KEY,nombre nvarchar(120));
INSERT @festivos VALUES
('20260101',N'Ano Nuevo'),('20260112',N'Reyes Magos'),('20260323',N'San Jose'),
('20260402',N'Jueves Santo'),('20260403',N'Viernes Santo'),('20260501',N'Dia del Trabajo'),
('20260518',N'Ascension'),('20260608',N'Corpus Christi'),('20260615',N'Sagrado Corazon'),
('20260629',N'San Pedro y San Pablo'),('20260720',N'Independencia'),('20260807',N'Batalla de Boyaca'),
('20260817',N'Asuncion'),('20261012',N'Diversidad Etnica y Cultural'),('20261102',N'Todos los Santos'),
('20261116',N'Independencia de Cartagena'),('20261208',N'Inmaculada Concepcion'),('20261225',N'Navidad');
;WITH fechas AS (
 SELECT CONVERT(date,'20250101',112) AS fecha
 UNION ALL SELECT DATEADD(day,1,fecha) FROM fechas WHERE fecha<CONVERT(date,'20271231',112)
), clasificado AS (
 SELECT f.fecha,CONVERT(bit,CASE WHEN DATEDIFF(day,CONVERT(date,'19000101',112),f.fecha)%7<5
     AND fest.fecha IS NULL THEN 1 ELSE 0 END) AS es_habil,fest.nombre
 FROM fechas f LEFT JOIN @festivos fest ON fest.fecha=f.fecha
)
INSERT cfg.dia_calendario(id_calendario,fecha,es_habil,acumulado_habil,nombre_festivo)
SELECT 1,fecha,es_habil,SUM(CONVERT(int,es_habil)) OVER(ORDER BY fecha ROWS UNBOUNDED PRECEDING),nombre
FROM clasificado OPTION(MAXRECURSION 0);
UPDATE cfg.calendario SET publicado=1 WHERE id_calendario=1;
INSERT cfg.perfil_entrada(id_perfil,nombre,version,version_parser,version_modelo,id_calendario,fecha_minima,fecha_maxima,publicado)
VALUES(1,'SABANA_CONTROL',1,'sabana-fechas/1.0','1.0.0',1,'20250101','20271231',0);
INSERT cfg.campo_entrada(id_perfil,campo,orden,tipo_dato,obligatorio,longitud_maxima,tipo_parte,bit_no_aplica)
VALUES
"""
rows=[]
dictionary=[]
for order,field in enumerate(FIELDS,1):
    kind='FECHA' if field.startswith('fecha_') else 'IDENTIFICADOR' if field in ('reparto_paciente','identificacion_paciente') else 'TEXTO'
    maximum=None if kind=='FECHA' else 50 if field=='identificacion_paciente' else 100 if field.startswith(('documento_','estado_')) or field in ('reparto_paciente','tipo_de_dictamen') else 150 if field.startswith('guia_') else 500
    required=field in ('fecha_audiencia','reparto_paciente','identificacion_paciente')
    part=None;bit=None
    for part_name in PARTS:
        for prefix,b,_ in ATTRS:
            if field==f'{prefix}_{part_name}':part,bit=part_name,b
        if field==NAMES.get(part_name):part,bit=part_name,1
    rows.append(f"(1,'{field}',{order},'{kind}',{int(required)},{maximum if maximum else 'NULL'},{quote(part) if part else 'NULL'},{bit if bit else 'NULL'})")
    dictionary.append([order+4,field,'ENTRADA',kind,maximum or '',part or '',bit or '',int(required)])
seed+=',\n'.join(rows)+";\nINSERT cfg.alias_columna(id_perfil,alias_normalizado,campo) SELECT id_perfil,CONVERT(nvarchar(255),campo),campo FROM cfg.campo_entrada;\nUPDATE cfg.perfil_entrada SET publicado=1 WHERE id_perfil=1;\nCOMMIT TRANSACTION;\nEND TRY\nBEGIN CATCH\nIF XACT_STATE()<>0 ROLLBACK TRANSACTION;\nTHROW;\nEND CATCH;\n"
(BASE/'02_catalogos.sql').write_text(seed,encoding='utf-8')

sql="""/* Vistas sin almacenamiento adicional. Ejecutar cada lote separado por GO. */
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO
CREATE OR ALTER FUNCTION cfg.fn_dias_habiles(@id_calendario smallint,@inicio date,@fin date)
RETURNS TABLE WITH SCHEMABINDING
AS RETURN (
 SELECT CASE WHEN @inicio IS NULL OR @fin IS NULL OR i.fecha IS NULL OR f.fecha IS NULL THEN CONVERT(int,NULL)
   WHEN @fin>=@inicio THEN f.acumulado_habil-i.acumulado_habil
   ELSE f.acumulado_habil-i.acumulado_habil+CONVERT(int,i.es_habil)-CONVERT(int,f.es_habil)
   END AS dias_habiles
 FROM (VALUES(1)) AS semilla(n)
 LEFT JOIN cfg.dia_calendario i ON i.id_calendario=@id_calendario AND i.fecha=@inicio
 LEFT JOIN cfg.dia_calendario f ON f.id_calendario=@id_calendario AND f.fecha=@fin
);
GO
CREATE OR ALTER VIEW rep.vw_fila_base
AS
SELECT f.id_fila,f.id_carga,f.id_caso,f.id_perfil,pe.id_calendario,
       CONCAT(N'Sala ',c.sala) AS sala,a.ruta_actual AS archivo_origen,f.numero_fila AS fila_origen,
       CONCAT(N'Sala ',c.sala,N'|',CONVERT(nvarchar(10),c.fecha_audiencia,23),N'|',c.reparto_paciente,N'|',c.identificacion_paciente) AS llave_caso,
"""
expressions={
 'fecha_audiencia':'CONVERT(nvarchar(10),c.fecha_audiencia,23)',
 'tipo_de_dictamen':'cd.tipo_de_dictamen','reparto_paciente':'c.reparto_paciente',
 'identificacion_paciente':'c.identificacion_paciente','nombre_paciente':'cd.nombre_paciente',
 'medico_ponente':'cd.medico_ponente','fecha_fijacion':'CONVERT(nvarchar(10),cd.fecha_fijacion,23)',
 'fecha_desfijacion':'CONVERT(nvarchar(10),cd.fecha_desfijacion,23)','entidad_remitente':'rem.nombre'
}
for idx,part in enumerate(PARTS):
    if part in NAMES:expressions[NAMES[part]]=f"CASE WHEN (p{idx}.mascara_no_aplica & 1)=1 THEN N'NO APLICA' ELSE e{idx}.nombre END"
    for prefix,bit,dbcolumn in ATTRS:
        value=f'n{idx}.{dbcolumn}'
        if prefix.startswith('fecha_'):value=f'CONVERT(nvarchar(10),{value},23)'
        expressions[f'{prefix}_{part}']=f"CASE WHEN (p{idx}.mascara_no_aplica & {bit})={bit} THEN N'NO APLICA' ELSE {value} END"
selects=[f"       COALESCE(x.[{field}],CONVERT(nvarchar(2000),{expressions[field]}) COLLATE DATABASE_DEFAULT) AS [{field}]" for field in FIELDS]
selects.extend(['       c.fecha_audiencia AS _fecha_audiencia','       cd.fecha_fijacion AS _fecha_fijacion','       cd.fecha_desfijacion AS _fecha_desfijacion'])
for idx,part in enumerate(PARTS):
    selects.extend([f'       n{idx}.fecha_envio_reportada AS _fecha_envio_{part}',f'       n{idx}.fecha_notificacion_reportada AS _fecha_notificacion_{part}'])
sql+=',\n'.join(selects)+"\nFROM ing.fila_origen f\nJOIN ing.carga ca ON ca.id_carga=f.id_carga AND ca.estado='PUBLICADA'\nJOIN ing.archivo a ON a.id_archivo=ca.id_archivo AND a.id_carga_vigente=ca.id_carga\nJOIN cfg.perfil_entrada pe ON pe.id_perfil=f.id_perfil\nJOIN core.caso c ON c.id_caso=f.id_caso\nJOIN core.caso_dato cd ON cd.id_caso_dato=f.id_caso_dato\nLEFT JOIN core.entidad rem ON rem.id_entidad=f.id_entidad_remitente\n"
for idx,part in enumerate(PARTS):
    sql+=f"LEFT JOIN ing.parte_fila p{idx} ON p{idx}.id_fila=f.id_fila AND p{idx}.tipo_parte='{part}'\nLEFT JOIN core.caso_parte cp{idx} ON cp{idx}.id_caso_parte=p{idx}.id_caso_parte\nLEFT JOIN core.entidad e{idx} ON e{idx}.id_entidad=cp{idx}.id_entidad\nLEFT JOIN core.notificacion_declarada n{idx} ON n{idx}.id_notificacion=p{idx}.id_notificacion\n"
sql+="OUTER APPLY (SELECT\n"+',\n'.join(f" MAX(CASE WHEN v.campo='{field}' THEN v.valor_original END) AS [{field}]" for field in FIELDS)+"\nFROM ing.valor_original v WHERE v.id_fila=f.id_fila AND v.mostrar_en_consolidado=1) x;\nGO\nCREATE OR ALTER VIEW rep.vw_consolidado_sabana\nAS\nSELECT\n"
metrics={};applies=[]
def add_metric(start,end,suffix,index):
    metrics['dias_cal_'+suffix]=f'DATEDIFF(day,{start},{end})'
    metrics['dias_hab_'+suffix]=f'h{index}.dias_habiles'
    applies.append(f'OUTER APPLY cfg.fn_dias_habiles(b.id_calendario,{start},{end}) h{index}')
add_metric('b._fecha_audiencia','b._fecha_fijacion','audiencia_a_fijacion',0)
add_metric('b._fecha_fijacion','b._fecha_desfijacion','fijacion_a_desfijacion',1)
for idx,part in enumerate(PARTS):
    add_metric('b._fecha_audiencia',f'b._fecha_envio_{part}',f'audiencia_a_envio_{part}',2+idx*2)
    add_metric('b._fecha_audiencia',f'b._fecha_notificacion_{part}',f'audiencia_a_notificacion_{part}',3+idx*2)
metrics['fecha_primer_envio_partes']='CONVERT(nvarchar(10),primera.fecha,23)'
add_metric('b._fecha_audiencia','primera.fecha','audiencia_a_primer_envio_partes',14)
sql+=',\n'.join(f'    {metrics.get(h,"b.["+h+"]")} AS [{h}]' for h in HEADERS)
sql+='\nFROM rep.vw_fila_base b\nOUTER APPLY (SELECT MIN(v.fecha) AS fecha FROM (VALUES '+','.join(f'(b._fecha_envio_{p})' for p in PARTS)+') v(fecha)) primera\n'+'\n'.join(applies)+';\nGO\n'
sql+="""CREATE OR ALTER VIEW rep.vw_notificacion_declarada_vigente
AS
/* Una misma observacion tipada puede tener multiples filas de procedencia.
   No selecciona arbitrariamente una version si fuentes vigentes se contradicen. */
SELECT DISTINCT nd.id_notificacion,cp.id_caso,cp.id_caso_parte,cp.tipo_parte,cp.id_entidad,
 nd.documento_notificacion,nd.estado_reportado,nd.fecha_notificacion_reportada,
 nd.fecha_envio_reportada,nd.guia_reportada,nd.fecha_recibido_reportada
FROM ing.parte_fila pf
JOIN ing.fila_origen f ON f.id_fila=pf.id_fila
JOIN ing.carga ca ON ca.id_carga=f.id_carga AND ca.estado='PUBLICADA'
JOIN ing.archivo a ON a.id_archivo=ca.id_archivo AND a.id_carga_vigente=ca.id_carga
JOIN core.caso_parte cp ON cp.id_caso_parte=pf.id_caso_parte
JOIN core.notificacion_declarada nd ON nd.id_notificacion=pf.id_notificacion;
GO
"""
(BASE/'03_vistas.sql').write_text(sql,encoding='utf-8')
with (BASE/'diccionario_salida.csv').open('w',encoding='utf-8-sig',newline='') as f:
    writer=csv.writer(f);writer.writerow(['orden','campo','procedencia','tipo_logico','longitud_maxima','parte','bit_no_aplica','obligatorio_entrada'])
    by_name={r[1]:r for r in dictionary}
    for i,h in enumerate(HEADERS,1):
        writer.writerow(by_name[h] if h in by_name else [i,h,'CALCULADA' if h.startswith('dias_') or h=='fecha_primer_envio_partes' else 'METADATO','ENTERO' if h.startswith('dias_') else 'TEXTO','','','',''])
print(json.dumps({'columnas_entrada':len(FIELDS),'columnas_salida':len(HEADERS),'partes':len(PARTS),'intervalos_habiles':len(applies)}))
