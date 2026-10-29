import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const root = process.argv[2] ? path.resolve(process.argv[2]) : path.join(path.dirname(fileURLToPath(import.meta.url)), 'resultados');
const read = async name => JSON.parse(await fs.readFile(path.join(root, name+'.json'), 'utf8'));
const [emails, attachments, people, issues] = await Promise.all([
  read('correos'), read('adjuntos'), read('participantes_cabecera'), read('incidencias')]);
if (!emails.length || emails.some(r=>!r.cuerpo_texto)) throw Error('Faltan correos reales o cuerpos extraídos');
const last = 8+emails.length;
const wb = Workbook.create();
const safe = v => typeof v === 'string' && /^\s*[=+@-]/.test(v) ? "'"+v : v ?? null;
// Excel stores wall clock values. Colombia columns explicitly show UTC-05:00.
const localDate = value => value ? new Date(value.slice(0,19)+'Z') : null;
function table(name, columns, data, row=1) {
  const sheet = wb.worksheets.add(name);
  sheet.showGridLines = false;
  const matrix = [columns.map(c=>c[0]), ...data.map(r=>columns.map(c=>safe(c[2] ? c[2](r) : r[c[1]])))];
  const range = sheet.getRangeByIndexes(row-1,0,matrix.length,columns.length);
  range.values = matrix;
  range.format.font = {name:'Arial',size:10,color:'#1F2937'};
  range.format.rowHeight = 28;
  range.format.verticalAlignment = 'center';
  sheet.getRangeByIndexes(row-1,0,1,columns.length).format = {
    fill:'#243B53',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},
    wrapText:true,rowHeight:42,horizontalAlignment:'center',verticalAlignment:'center'};
  const letter = n => n <= 26 ? String.fromCharCode(64+n) : letter(Math.floor((n-1)/26))+letter((n-1)%26+1);
  sheet.tables.add(`A${row}:${letter(columns.length)}${row+matrix.length-1}`,true,
    'Tabla'+name.replace(/[^A-Za-z]/g,''));
  for(let i=0;i<columns.length;i++) {
    sheet.getRangeByIndexes(row-1,i,matrix.length,1).format.columnWidth = columns[i][3] ?? 24;
    if(columns[i][4]) sheet.getRangeByIndexes(row,i,data.length,1).setNumberFormat(columns[i][4]);
  }
  sheet.freezePanes.freezeRows(row);
  sheet.freezePanes.freezeColumns(1);
  return sheet;
}
const cols = [
 ['ID piloto','id_piloto',null,12],['Asunto','asunto',null,75],
 ['Envío Colombia (UTC-05)','envio_colombia',r=>localDate(r.envio_colombia),25,'dd/mm/yyyy hh:mm:ss'],
 ['Recepción buzón Colombia (UTC-05)','recepcion_buzon_colombia',r=>localDate(r.recepcion_buzon_colombia),25,'dd/mm/yyyy hh:mm:ss'],
 ['Remitente','remitente_nombre',null,30],['Remitente SMTP','remitente_smtp',null,42],
 ['Para','para',null,65],['CC','cc',null,45],['CCO','cco',null,35],
 ['Adjuntos reportados','adjuntos_reportados',null,15,'0'],
 ['Carpeta origen','carpeta',null,65],['ID elemento PST','id_pst',r=>String(r.id_pst),22],
 ['Índice carpeta (desde 0)','indice_carpeta_0',null,18,'0'],
 ['Message-ID','message_id',null,65],['Clase MAPI','clase_mensaje',null,35],
 ['Fecha cabecera original','date_cabecera_original',null,40],
 ['Envío UTC ISO','envio_utc',null,30],['Recepción buzón UTC ISO','recepcion_buzon_utc',null,30],
 ['En respuesta a','in_reply_to',null,65],['Referencias','references',null,65],
 ['Reply-To','reply_to',null,40],['Fuente Para','para_fuente',null,40],
 ['Cabeceras presentes','cabeceras_presentes',r=>r.cabeceras_presentes?'Sí':'No',20],
 ['Message-ID repetido en muestra','message_id_repetido_muestra',r=>r.message_id_repetido_muestra?'Sí':'No',22],
 ['Estado extracción','estado_extraccion',null,24],
 ['Confirmación de lectura','confirmacion_lectura',null,26],
 ['Tipo de reporte','tipo_reporte',null,32],
 ['Evidencia de clasificación','evidencia_clasificacion',null,65],
 ['Nombres de adjuntos','adjuntos_nombres',null,70],
 ['Cuerpo (vista breve)','cuerpo_texto',r=>r.cuerpo_texto ? r.cuerpo_texto.replace(/\s+/g,' ').slice(0,180)+(r.cuerpo_texto.length>180?'… Ver hoja Cuerpos.':'') : null,80],
 ['Fuente del cuerpo','cuerpo_fuente',null,30],
 ['Archivo cuerpo completo','cuerpo_archivo',null,30],
 ['Solicita acuse de lectura (cabecera)','solicita_confirmacion_lectura',r=>r.solicita_confirmacion_lectura?'Sí':'No',25],
 ['Acuse manual posible','acuse_manual_posible',r=>r.acuse_manual_posible?'Sí; revisar':'No identificado',26],
 ['Indicio de acuse manual','evidencia_acuse_manual',null,35]
];
const main = table('Correos',cols,emails,8);
main.getRange('A2').values = [['Piloto de correos. Junta Regional Bogotá']];
main.getRange('A2').format.font = {name:'Arial',size:14,bold:true,color:'#243B53'};
main.getRange('A3').values = [['Fuente: JUNIO Y JULIO 2026.pst. Muestra técnica, sin inferencia estadística.']];
main.getRange('A4').values = [['Correos']];
main.getRange('B4').formulas = [[`=COUNTA(A9:A${last})`]];
main.getRange('C4').values = [['Adjuntos reportados']];
main.getRange('D4').formulas = [[`=SUM(J9:J${last})`]];
main.getRange('A5').values = [[`${emails.length} correos extraídos. ${issues.length} incidencias de selección. Ver Incidencias.`]];
main.getRange('A6').values = [['Recepción corresponde al buzón exportado. No acredita entrega al destinatario. Celdas vacías: dato no disponible.']];
main.getRange(`B9:B${last}`).format.wrapText = true;
main.getRange(`A9:AI${last}`).format.rowHeight = 65;
main.getRange(`Z9:AI${last}`).format.wrapText = true;
main.tabColor = '#243B53';
const segments = [];
for (const r of emails) {
  const chunks = [];
  let chunk = '';
  let lines = 0;
  for (const line of (r.cuerpo_texto || '').split('\n')) {
    for (let i=0;i<Math.max(1,line.length);i+=110) {
      const part = line.slice(i,i+110);
      if (lines>=8) {chunks.push(chunk);chunk='';lines=0;}
      chunk += (lines?'\n':'')+part;
      lines++;
    }
  }
  if(chunk) chunks.push(chunk);
  for(let i=0;i<chunks.length;i++) segments.push({id:r.id_piloto,segmento:i+1,
    total:chunks.length,asunto:r.asunto,cuerpo:chunks[i],fuente:r.cuerpo_fuente});
}
const bodySheet = table('Cuerpos',[
 ['ID piloto','id',null,12],['Segmento','segmento',null,12,'0'],['Total segmentos','total',null,16,'0'],
 ['Asunto','asunto',null,65],['Cuerpo del correo','cuerpo',null,115],['Fuente','fuente',null,28]
],segments);
bodySheet.getRangeByIndexes(1,3,segments.length,2).format.wrapText = true;
bodySheet.getRangeByIndexes(1,0,segments.length,6).format.rowHeight = 135;
bodySheet.getRangeByIndexes(1,3,segments.length,2).format.verticalAlignment = 'top';
table('Adjuntos',[
 ['ID piloto','id_piloto',null,12],['Índice adjunto (desde 0)','indice_adjunto_0',null,20,'0'],
 ['Nombre','nombre',null,90],['Tamaño informado (bytes)','tamano_bytes_lector',null,25,'#,##0'],
 ['MIME','mime',null,35],['Método MAPI','metodo_mapi',null,18,'0'],
 ['Content-ID','content_id',null,50],['Oculto MAPI','oculto_mapi',r=>r.oculto_mapi==null?null:r.oculto_mapi?'Sí':'No',18]
],attachments);
table('Participantes',[
 ['ID piloto','id_piloto',null,12],['Rol','rol',null,18],['Orden','orden',null,12,'0'],
 ['Nombre','nombre',null,45],['Dirección','direccion',null,60],['Fuente','fuente',null,30]
],people);
table('Incidencias',[
 ['Carpeta origen','carpeta',null,90],['Índice carpeta (desde 0)','indice',null,20,'0'],
 ['Tipo','tipo',null,25],['Detalle técnico','detalle',null,140]
],issues);
wb.recalculate();
const check = await wb.inspect({kind:'table',range:'Correos!A4:D4',include:'values,formulas',tableMaxRows:1,tableMaxCols:4,maxChars:1000});
console.log(check.ndjson);
const errors = await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!',
 options:{useRegex:true,maxResults:20},summary:'Errores de fórmulas'});
console.log(errors.ndjson);
await (await SpreadsheetFile.exportXlsx(wb)).save(path.join(root,`piloto_${emails.length}_correos.xlsx`));
for (const [name,range] of [['Correos','Z8:AC10'],['Cuerpos','A1:F4'],['Adjuntos','A1:D5'],['Participantes','A1:F5'],['Incidencias','A1:C3']]) {
  const png = await wb.render({sheetName:name,range,scale:1,format:'png'});
  await fs.writeFile(path.join(root,'vista_'+name+'.png'),new Uint8Array(await png.arrayBuffer()));
}
console.log('Excel y vistas generados.');
