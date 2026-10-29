"""Piloto local de metadatos PST. No crea bases de datos ni extrae adjuntos."""
import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, deque
from datetime import datetime, timezone, timedelta
from email import policy
from email.parser import Parser
from email.utils import getaddresses
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / '.deps'))


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def properties(item, tags):
    """Lee solo propiedades solicitadas; no recupera cuerpos ni binarios de adjuntos."""
    result = {}
    if not item.number_of_record_sets:
        return result
    rs = item.get_record_set(0)
    for i in range(rs.number_of_entries):
        e = rs.get_entry(i)
        if e.entry_type not in tags:
            continue
        if e.value_type in (0x1e, 0x1f):
            result[e.entry_type] = e.data_as_string
        elif e.value_type in (2, 3, 0x14):
            result[e.entry_type] = e.data_as_integer
        elif e.value_type == 0xb:
            result[e.entry_type] = e.data_as_boolean
        elif e.value_type == 0x102:
            result[e.entry_type] = e.data.hex()
    return result


def has_properties(item, tags):
    if not item.number_of_record_sets:
        return []
    rs = item.get_record_set(0)
    return [hex(e.entry_type) for e in (rs.get_entry(i) for i in range(rs.number_of_entries))
            if e.entry_type in tags]


def date_values(value):
    if value is None:
        return None, None
    # libpff MAPI timestamps are UTC. Keep original FILETIME separately as well.
    utc = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return utc.isoformat(), utc.astimezone(timezone(timedelta(hours=-5))).isoformat()


def csv_file(path, rows, fields):
    # Quote formula-like text for safe opening in Excel. Exact values remain in JSON.
    def safe(v):
        if v is None:
            return ''
        if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@')):
            return "'" + v
        return v
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter=';')
        writer.writeheader()
        writer.writerows({k: safe(r.get(k)) for k in fields} for r in rows)


def run(source, limit=20, output=None):
    import pypff
    source = source.resolve()
    before = source.stat()
    out = Path(output) if output else HERE / 'resultados'
    out.mkdir(parents=True, exist_ok=True)
    print('Abriendo PST en modo rb...', flush=True)
    messages, attachments, recipients, issues, folders = [], [], [], [], []
    with source.open('rb') as stream:
        pst = pypff.file()
        pst.open_file_object(stream)
        try:
            todo = deque([(pst.get_root_folder(), '')])
            while todo:
                folder, parent = todo.popleft()
                path = parent + '/' + (folder.name or '(raiz)')
                folders.append({'carpeta': path, 'id_carpeta': folder.identifier,
                                'mensajes_reportados': folder.number_of_sub_messages,
                                '_folder': folder})
                todo.extend((folder.get_sub_folder(i), path) for i in range(folder.number_of_sub_folders))
            print('Carpetas inventariadas:', len(folders), flush=True)
            # Round-robin across nonempty folders, evenly spaced positions in each folder.
            candidates = []
            for f in sorted(folders, key=lambda f: f['carpeta']):
                n = f['mensajes_reportados']
                if n:
                    count = min(limit, n)
                    indexes = [round(j * (n - 1) / max(1, count - 1)) for j in range(count)]
                    candidates.append((f, deque(indexes)))
            examined = 0
            while len(messages) < limit and any(q for _, q in candidates):
                for f, queue in candidates:
                    if not queue or len(messages) == limit:
                        continue
                    index = queue.popleft()
                    examined += 1
                    case = f'P-{len(messages)+1:03d}'
                    row = {'id_piloto': case, 'archivo': source.name, 'carpeta': f['carpeta'],
                           'id_carpeta': f['id_carpeta'], 'indice_carpeta_0': index}
                    try:
                        m = f['_folder'].get_sub_message(index)
                        row['id_pst'] = m.identifier
                        props = properties(m, {0x001a, 0x0c1a, 0x0c1e, 0x0c1f, 0x5d01,
                                               0x1035, 0x1042, 0x0071, 0x0e07, 0x0e08,
                                               0x0e02, 0x0e03, 0x0e04})
                        cls = props.get(0x001a) or ''
                        if cls and not (cls.upper().startswith('IPM.NOTE') or cls.upper().startswith('REPORT.')):
                            issues.append({'carpeta': f['carpeta'], 'indice': index,
                                           'tipo': 'elemento_no_correo', 'detalle': cls})
                            continue
                        raw = m.transport_headers
                        if isinstance(raw, bytes):
                            raw = raw.decode('utf-8', errors='replace')
                        header = Parser(policy=policy.default).parsestr(raw or '', headersonly=True)
                        row.update({
                            'clase_mensaje': cls or None,
                            'message_id': props.get(0x1035) or str(header.get('Message-ID', '')) or None,
                            'asunto': m.subject, 'remitente_nombre': m.sender_name,
                            'remitente_mapi_direccion': props.get(0x0c1f),
                            'remitente_mapi_tipo': props.get(0x0c1e),
                            'remitente_smtp': props.get(0x5d01),
                            'from_cabecera': str(header.get('From', '')) or None,
                            'para': str(header.get('To', '')) or props.get(0x0e04),
                            'cc': str(header.get('Cc', '')) or props.get(0x0e03),
                            'cco': str(header.get('Bcc', '')) or props.get(0x0e02),
                            'para_fuente': 'cabecera' if header.get('To') else 'MAPI display; puede contener solo nombres',
                            'reply_to': str(header.get('Reply-To', '')) or None,
                            'date_cabecera_original': str(header.get('Date', '')) or None,
                            'in_reply_to': str(header.get('In-Reply-To', '')) or props.get(0x1042),
                            'references': str(header.get('References', '')) or None,
                            'tema_conversacion': m.conversation_topic,
                            'indice_conversacion_hex': props.get(0x0071),
                            'flags_mapi': props.get(0x0e07), 'tamano_mensaje_mapi': props.get(0x0e08),
                            'adjuntos_reportados': m.number_of_attachments,
                            'propiedades_cuerpo_presentes': ', '.join(has_properties(m, {0x1000, 0x1009, 0x1013})),
                            'cabeceras_presentes': bool(raw),
                        })
                        for name, attr in [('envio', 'client_submit_time'), ('recepcion_buzon', 'delivery_time')]:
                            row[name+'_utc'], row[name+'_colombia'] = date_values(getattr(m, attr))
                            row[name+'_filetime'] = str(getattr(m, 'get_'+attr+'_as_integer')())
                        for field in ('From', 'To', 'Cc', 'Bcc', 'Reply-To'):
                            for order, (name, address) in enumerate(getaddresses([str(x) for x in header.get_all(field, [])]), 1):
                                recipients.append({'id_piloto': case, 'rol': field, 'orden': order,
                                                   'nombre': name or None, 'direccion': address or None,
                                                   'fuente': 'cabecera_transporte'})
                        # Keep original header text as returned by libpff, without body or attachments.
                        if raw:
                            header_path = out / 'cabeceras'
                            header_path.mkdir(exist_ok=True)
                            (header_path / (case+'.txt')).write_text(raw, encoding='utf-8')
                            row['cabeceras_sha256_utf8'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()
                        for i in range(m.number_of_attachments):
                            a = m.get_attachment(i)
                            ap = properties(a, {0x3704, 0x3707, 0x370e, 0x3705, 0x3712, 0x7ffe})
                            attachments.append({'id_piloto': case, 'indice_adjunto_0': i,
                                                'nombre': ap.get(0x3707) or ap.get(0x3704),
                                                'tamano_bytes_lector': a.size, 'mime': ap.get(0x370e),
                                                'metodo_mapi': ap.get(0x3705), 'content_id': ap.get(0x3712),
                                                'oculto_mapi': ap.get(0x7ffe)})
                        row['estado_extraccion'] = 'extraido'
                        messages.append(row)
                        print(case, 'extraido', flush=True)
                    except Exception as e:
                        issues.append({'carpeta': f['carpeta'], 'indice': index,
                                       'tipo': 'error_extraccion', 'detalle': str(e)})
                        # No partial attachment/recipient rows for rejected messages.
                        attachments[:] = [a for a in attachments if a['id_piloto'] != case]
                        recipients[:] = [a for a in recipients if a['id_piloto'] != case]
            dump(out/'carpetas.json', [{k:v for k,v in f.items() if k != '_folder'} for f in folders])
        finally:
            pst.close()
    after = source.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise RuntimeError('La fuente cambio durante la lectura; resultados pendientes de validacion.')
    if len(messages) != limit:
        dump(out/'incidencias.json', issues)
        raise RuntimeError(f'Solo se extrajeron {len(messages)} correos. Revisar incidencias.')
    ids = [r['id_pst'] for r in messages]
    assert len(set(ids)) == limit, 'La seleccion contiene el mismo elemento PST mas de una vez'
    for r in messages:
        assert r['adjuntos_reportados'] == sum(a['id_piloto'] == r['id_piloto'] for a in attachments)
    duplicates = Counter(r['message_id'] for r in messages if r['message_id'])
    for r in messages:
        r['message_id_repetido_muestra'] = bool(r['message_id'] and duplicates[r['message_id']] > 1)
    dump(out/'correos.json', messages)
    dump(out/'adjuntos.json', attachments)
    dump(out/'participantes_cabecera.json', recipients)
    dump(out/'incidencias.json', issues)
    csv_file(out/'correos.csv', messages, list(dict.fromkeys(k for r in messages for k in r)))
    if attachments:
        csv_file(out/'adjuntos.csv', attachments, list(attachments[0]))
    if recipients:
        csv_file(out/'participantes_cabecera.csv', recipients, list(recipients[0]))
    summary = {
        'fuente': source.name, 'tercero': 'Junta Regional Bogota',
        'archivo_bytes': before.st_size, 'mtime_utc': datetime.fromtimestamp(before.st_mtime, timezone.utc).isoformat(),
        'fecha_extraccion_utc': datetime.now(timezone.utc).isoformat(), 'version_libpff': pypff.get_version(),
        'seleccion': f'Tecnica, no estadistica. Alternancia entre carpetas no vacias ordenadas por ruta; hasta {limit} posiciones equidistantes por carpeta, indices desde cero; se detiene en {limit} correos extraidos.',
        'correos_extraidos': len(messages), 'candidatos_examinados': examined,
        'carpetas_inventariadas': len(folders),
        'elementos_reportados_carpetas': sum(f['mensajes_reportados'] for f in folders),
        'adjuntos_metadata': len(attachments), 'participantes_cabecera': len(recipients),
        'incidencias': len(issues), 'fuente_tamano_fecha_sin_cambios': True,
        'huella_pst': 'Pendiente; no se calculo SHA-256 del archivo completo.',
        'faltantes': {k: sum(r.get(k) in (None, '') for r in messages) for k in
                      ['message_id', 'asunto', 'remitente_smtp', 'para', 'envio_utc', 'recepcion_buzon_utc']},
        'limites': ['No se extraen cuerpos ni contenido de adjuntos.',
                   'Participantes solo desde cabeceras; no constituye censo de destinatarios MAPI.',
                   'Las fechas de recepcion son del buzon exportado; no acreditan entrega al destinatario.',
                   'No se filtra por periodo ni se clasifican notificaciones en este piloto.',
                   'Inventario de carpetas no verifica integridad completa ni incluye recuperacion de elementos eliminados.']}
    dump(out/'resumen.json', summary)
    print(json.dumps(summary, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pst', default=str(HERE.parents[1] / 'JUNIO Y JULIO 2026.pst'))
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--out')
    args = parser.parse_args()
    if args.limit < 1:
        parser.error('--limit debe ser positivo')
    run(Path(args.pst), args.limit, args.out)
