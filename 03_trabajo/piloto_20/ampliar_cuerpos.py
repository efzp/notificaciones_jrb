"""Amplia los mismos 20 elementos, por carpeta/indice e ID PST verificado."""
import argparse
import json
import re
from collections import Counter, deque
from html.parser import HTMLParser
from pathlib import Path
from email import policy
from email.parser import Parser
from extraer_piloto import HERE, dump, properties, csv_file


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        if tag in ('p', 'div', 'br', 'tr', 'li', 'h1', 'h2', 'h3'):
            self.parts.append('\n')
        if tag in ('td', 'th'):
            self.parts.append('\t')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip = max(0, self.skip-1)
        if tag in ('p', 'div', 'tr', 'li'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def decode(raw, cp=None, html=False):
    if isinstance(raw, str):
        return raw, 'unicode_del_lector'
    choices = []
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        choices.append('utf-16')
    if html:
        meta = re.search(br'charset\s*=\s*["\x27]?([A-Za-z0-9_-]+)', raw[:8192], re.I)
        if meta:
            choices.append(meta.group(1).decode('ascii'))
    choices.append('utf-8-sig')
    if cp:
        choices.append('utf-8' if cp == 65001 else f'cp{cp}')
    choices.append('cp1252')
    for name in dict.fromkeys(choices):
        try:
            return raw.decode(name), name
        except (UnicodeError, LookupError):
            pass
    return raw.decode('utf-8', errors='replace'), 'utf-8_con_reemplazos_revisar'


def classify(row, headers, body):
    cls = (row.get('clase_mensaje') or '').upper()
    ct = headers.get('Content-Type')
    mime = str(ct or '')
    if cls.startswith('REPORT.') and cls.endswith('.IPNRN'):
        return 'Sí (técnica)', 'Confirmación de lectura', 'Clase MAPI: '+cls
    if cls.startswith('REPORT.') and cls.endswith('.IPNNRN'):
        return 'No (otro reporte)', 'Confirmación de no lectura', 'Clase MAPI: '+cls
    if cls.startswith('REPORT.') and cls.endswith('.NDR'):
        return 'No (otro reporte)', 'No entrega', 'Clase MAPI: '+cls
    if cls.startswith('REPORT.') and cls.endswith('.DR'):
        return 'No (otro reporte)', 'Entrega o relevo', 'Clase MAPI: '+cls+'; no es confirmación de lectura'
    mdn = headers.get_content_type() == 'multipart/report' and (
        headers.get_param('report-type', '') or '').lower() == 'disposition-notification'
    if mdn:
        # A disposition-notification can also report deletion or processing.
        return 'Revisar MDN', 'Notificación de disposición', 'Content-Type: '+mime+'; falta verificar disposición MDN'
    subject = row.get('asunto') or ''
    if re.match(r'^\s*(le[ií]do|read|confirmaci[oó]n de lectura|read receipt)\s*[:：]', subject, re.I):
        return 'Posible; revisar', 'Indicio por asunto', 'Prefijo de asunto; sin clase MAPI o disposición MDN que lo confirme'
    return 'No identificada', 'Sin señal suficiente', 'Sin clase MAPI de lectura ni señal MDN o prefijo de asunto reconocido'


def run(output=None):
    import pypff
    out = Path(output) if output else HERE / 'resultados'
    source = HERE.parents[1] / 'JUNIO Y JULIO 2026.pst'
    rows = json.loads((out/'correos.json').read_text(encoding='utf-8'))
    attachments = json.loads((out/'adjuntos.json').read_text(encoding='utf-8'))
    initial_ids = [(r['id_piloto'], r['id_pst']) for r in rows]
    stat = source.stat()
    bodies = out/'cuerpos'
    bodies.mkdir(exist_ok=True)
    errors = []
    with source.open('rb') as stream:
        pst = pypff.file()
        pst.open_file_object(stream)
        try:
            folder_map = {}
            todo = deque([(pst.get_root_folder(), '')])
            while todo:
                folder, parent = todo.popleft()
                path = parent+'/'+(folder.name or '(raiz)')
                folder_map[path] = folder
                todo.extend((folder.get_sub_folder(i), path) for i in range(folder.number_of_sub_folders))
            for row in rows:
                m = folder_map[row['carpeta']].get_sub_message(row['indice_carpeta_0'])
                assert m.identifier == row['id_pst'], 'El elemento PST ya no coincide'
                cp = properties(m, {0x3fde, 0x3ffd})
                formats, decoded = [], {}
                for fmt, attr in [('texto', 'plain_text_body'), ('html', 'html_body'), ('rtf', 'rtf_body')]:
                    try:
                        raw = getattr(m, attr)
                        if not raw:
                            continue
                        suffix = {'texto':'plain.bin', 'html':'html', 'rtf':'rtf'}[fmt]
                        original = raw.encode('utf-8') if isinstance(raw, str) else raw
                        (bodies/(row['id_piloto']+'.'+suffix)).write_bytes(original)
                        formats.append(fmt)
                        if fmt != 'rtf':
                            text, encoding = decode(raw, cp.get(0x3fde) or cp.get(0x3ffd), fmt=='html')
                            decoded[fmt] = text
                            row['codificacion_'+fmt] = encoding
                    except Exception as e:
                        errors.append({'id_piloto':row['id_piloto'],'formato':fmt,'error':str(e)})
                body = decoded.get('texto', '')
                origin = 'MAPI texto' if body else None
                if not body and decoded.get('html'):
                    parser = HTMLText()
                    parser.feed(decoded['html'])
                    body = '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
                    origin = 'Texto derivado de HTML'
                row['cuerpo_texto'] = body or None
                row['cuerpo_fuente'] = origin
                row['cuerpo_estado'] = 'Extraído' if body else 'Sin texto recuperado; revisar' if formats else 'Sin cuerpo disponible'
                row['cuerpo_formatos'] = ', '.join(formats)
                row['cuerpo_caracteres'] = len(body)
                row['cuerpo_archivo'] = 'cuerpos/'+row['id_piloto']+'.txt' if body else None
                if body:
                    (bodies/(row['id_piloto']+'.txt')).write_text(body, encoding='utf-8')
                row['adjuntos_nombres'] = '\n'.join(a['nombre'] or '[Nombre no disponible, índice '+str(a['indice_adjunto_0'])+']'
                    for a in attachments if a['id_piloto'] == row['id_piloto'])
                raw_header = m.transport_headers or ''
                if isinstance(raw_header, bytes):
                    raw_header = raw_header.decode('utf-8', errors='replace')
                headers = Parser(policy=policy.default).parsestr(raw_header, headersonly=True)
                verdict, kind, evidence = classify(row, headers, body)
                row['confirmacion_lectura'] = verdict
                row['tipo_reporte'] = kind
                row['evidencia_clasificacion'] = evidence
                leading = re.split(r'(?im)^(?:El .{0,180}escribi[oó]:|On .{0,180}wrote:|De:|From:|_{5,}|-{2,}\s*Original Message)', body)[0][:800]
                manual = None if (row.get('clase_mensaje') or '').upper().startswith('REPORT.') else re.search(
                    r'\b(?:confirmo\s+(?:el\s+)?recibido|notifico\s+recibido|acuso\s+(?:el\s+)?recibo|confirmo\s+(?:la\s+)?recepci[oó]n|he\s+le[ií]do)\b', leading, re.I)
                row['acuse_manual_posible'] = bool(manual)
                row['evidencia_acuse_manual'] = manual.group(0) if manual else None
                row['solicita_confirmacion_lectura'] = bool(headers.get('Disposition-Notification-To') or headers.get('X-Confirm-Reading-To'))
                print(row['id_piloto'], row['cuerpo_estado'], len(body), verdict, flush=True)
        finally:
            pst.close()
    assert initial_ids == [(r['id_piloto'],r['id_pst']) for r in rows]
    assert source.stat().st_size == stat.st_size and source.stat().st_mtime_ns == stat.st_mtime_ns
    dump(out/'correos.json', rows)
    fields = list(dict.fromkeys(k for r in rows for k in r))
    csv_file(out/'correos.csv', rows, fields)
    dump(out/'incidencias_cuerpo.json', errors)
    summary = json.loads((out/'resumen.json').read_text(encoding='utf-8'))
    summary['ampliacion_cuerpos'] = {
        'correos_misma_muestra':len(rows), 'cuerpos_texto_extraidos':sum(bool(r['cuerpo_texto']) for r in rows),
        'clasificacion_lectura':dict(Counter(r['confirmacion_lectura'] for r in rows)),
        'acuses_manuales_posibles':sum(r['acuse_manual_posible'] for r in rows),
        'errores_cuerpo':len(errors), 'cuerpo_max_caracteres':max(r['cuerpo_caracteres'] for r in rows)}
    summary['limites'] = [x for x in summary['limites'] if x != 'No se extraen cuerpos ni contenido de adjuntos.']
    summary['limites'].extend(['Se extraen cuerpos del mensaje; no contenido binario de adjuntos.',
        'La identificación de confirmación de lectura usa clase MAPI y cabeceras; asunto solo da indicios.',
        'No identificada no demuestra ausencia de lectura. Solicitar confirmación no equivale a recibirla.'])
    dump(out/'resumen.json', summary)
    print(json.dumps(summary['ampliacion_cuerpos'],ensure_ascii=True),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out')
    run(parser.parse_args().out)
