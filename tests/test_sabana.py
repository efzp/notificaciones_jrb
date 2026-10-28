from __future__ import annotations

import base64
import hashlib
import json
import time
import unittest
from dataclasses import replace
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED

from openpyxl import Workbook
from openpyxl.utils.datetime import CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900, to_excel

from sabana.database import SqlRepository, retryable_sql, sql_number
from sabana.domain import AppError, Payload, Profile, fact_hash
from sabana.excel import parse_date, parse_workbook

BASE = Path(__file__).resolve().parents[1]


def profile():
    return Profile.from_json(json.loads((BASE / "profile.example.json").read_text(encoding="utf-8")))


def row(**changes):
    result = {f.campo: None for f in profile().fields}
    result.update(fecha_audiencia="2026-03-04", reparto_paciente="R-001", identificacion_paciente="001234",
                  nombre_paciente="PERSONA SINTETICA", medico_ponente="MEDICO SINTETICO")
    result.update(changes)
    return result


def excel(rows=None, headers=None, inflated=False, epoch=CALENDAR_WINDOWS_1900, formats=None):
    # Libros sinteticos en memoria; nunca se modifican los archivos del usuario.
    workbook = Workbook()
    workbook.epoch = epoch
    sheet = workbook.active
    sheet.title = "hoja1"
    names = [f.campo for f in profile().fields]
    sheet.append(headers or names)
    for values in rows or [row()]:
        sheet.append([values.get(name) for name in names])
    for coordinate, fmt in (formats or {}).items():
        sheet[coordinate].number_format = fmt
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    if not inflated:
        return stream.getvalue()
    output = BytesIO()
    with ZipFile(stream) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for info in source.infolist():
            value = source.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                import re
                value = re.sub(br'<dimension ref="[^"]+"', b'<dimension ref="A1:XFD1048576"', value)
            target.writestr(info, value)
    return output.getvalue()


def payload(content=None, **changes):
    document = dict(drive_id="drive", item_id="item", nombre_archivo="control.xlsx", ruta_sharepoint="/Sala 1/control.xlsx",
                    version_origen="1.0", fecha_modificacion_origen="2026-10-26T15:30:00.1234567Z", sala=1, id_perfil=1,
                    contenido_base64=base64.b64encode(content or excel()).decode("ascii"))
    document.update(changes)
    return Payload.from_json(document)


class ParserTests(unittest.TestCase):
    def test_original_and_two_insurers_share_case_facts(self):
        a = row(aseguradora="ASEGURADORA A", estado_aseguradora="Se notifico", fecha_envio_aseguradora="20260312")
        b = row(aseguradora="ASEGURADORA B", estado_aseguradora="Aviso", fecha_envio_aseguradora="20260312")
        parsed = parse_workbook(excel([a, b]), profile())
        self.assertFalse(parsed.blocked)
        self.assertEqual(parsed.total_rows, 2)
        self.assertEqual([r.number for r in parsed.rows], [2, 3])
        attrs = ("nombre_paciente", "medico_ponente", "tipo_de_dictamen", "fecha_fijacion", "fecha_desfijacion")
        self.assertEqual(fact_hash({k: parsed.rows[0].values[k] for k in attrs}),
                         fact_hash({k: parsed.rows[1].values[k] for k in attrs}))
        self.assertNotEqual(parsed.rows[0].values["aseguradora"], parsed.rows[1].values["aseguradora"])

    def test_header_recovery_is_generic(self):
        for name, value in (("eps", "EPS SINTETICA"), ("fecha_envio_arl", "2026-03-06"),
                            ("tipo_de_dictamen", "DICTAMEN"), ("fecha_audiencia", "2026-03-04")):
            with self.subTest(name=name):
                headers = [f.campo for f in profile().fields]
                headers[headers.index(name)] = "NO APLICA"
                parsed = parse_workbook(excel([row(**{name: value})], headers=headers), profile())
                self.assertFalse(parsed.blocked)
                self.assertEqual(parsed.structure["inferencias"][0]["campo"], name)
                self.assertEqual(parsed.structure["inferencias"][0]["anclas"], 49)

    def test_missing_header_without_value_evidence_blocks(self):
        headers = [f.campo for f in profile().fields]
        headers[headers.index("eps")] = "DESCONOCIDO"
        self.assertTrue(parse_workbook(excel(headers=headers), profile()).blocked)

    def test_duplicate_header_blocks(self):
        headers = [f.campo for f in profile().fields]
        headers[29] = "arl"
        parsed = parse_workbook(excel(headers=headers), profile())
        self.assertTrue(parsed.blocked)
        self.assertIn("ENCABEZADO_DUPLICADO", {i.codigo for i in parsed.issues})

    def test_order_independent_mapping(self):
        headers = [f.campo for f in profile().fields]
        headers[0], headers[1] = headers[1], headers[0]
        values = row(fecha_audiencia="DICTAMEN", tipo_de_dictamen="2026-03-04")
        parsed = parse_workbook(excel([values], headers=headers), profile())
        self.assertFalse(parsed.blocked)
        self.assertEqual(parsed.rows[0].values["fecha_audiencia"], date(2026, 3, 4))

    def test_inflated_dimension_does_not_expand_rows(self):
        parsed = parse_workbook(excel(inflated=True), profile())
        self.assertFalse(parsed.blocked)
        self.assertEqual(parsed.total_rows, 1)

    def test_data_in_last_excel_column_does_not_expand_the_input(self):
        content, output = excel(), BytesIO()
        with ZipFile(BytesIO(content)) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
            for info in source.infolist():
                value = source.read(info.filename)
                if info.filename == "xl/worksheets/sheet1.xml":
                    value = value.replace(b'</row></sheetData>', b'<c r="XFD2" t="inlineStr"><is><t>NO APLICA</t></is></c></row></sheetData>')
                target.writestr(info, value)
        parsed = parse_workbook(output.getvalue(), profile())
        self.assertFalse(parsed.blocked)
        self.assertEqual(parsed.total_rows, 1)
        self.assertEqual(parsed.structure["columnas_fuera_del_bloque"]["ultima_columna"], 16384)
        self.assertIn("COLUMNAS_FUERA_DEL_BLOQUE", {i.codigo for i in parsed.issues})

    def test_formats_and_both_excel_epochs(self):
        minimum, maximum, expected = date(2025, 1, 1), date(2027, 12, 31), date(2026, 6, 8)
        for value in ("2026-06-08", "2026/06/08", "08/06/2026", "08-06-2026", "08.06.2026",
                      "20260608", 20260608, 20260608.0, expected, datetime(2026, 6, 8), "2026-06-08T10:30:00Z"):
            self.assertEqual(parse_date(value, CALENDAR_WINDOWS_1900, minimum, maximum)[0], expected, value)
        for epoch in (CALENDAR_WINDOWS_1900, CALENDAR_MAC_1904):
            serial = to_excel(expected, epoch)
            self.assertEqual(parse_date(serial, epoch, minimum, maximum)[0], expected)
            parsed = parse_workbook(excel([row(fecha_envio_arl=serial)], epoch=epoch), profile())
            self.assertEqual(parsed.rows[0].values["fecha_envio_arl"], expected)

    def test_optional_bad_date_preserves_original(self):
        parsed = parse_workbook(excel([row(fecha_envio_arl="8202-08-21")]), profile())
        self.assertFalse(parsed.blocked)
        self.assertIsNone(parsed.rows[0].values["fecha_envio_arl"])
        original = parsed.rows[0].originals[0]
        self.assertEqual(original.valor_original, "8202-08-21")
        self.assertTrue(original.mostrar_en_consolidado)
        self.assertEqual(parsed.issues[0].codigo, "FECHA_FUERA_RANGO")

    def test_required_bad_date_blocks_entire_file(self):
        parsed = parse_workbook(excel([row(), row(fecha_audiencia="31/02/2026")]), profile())
        self.assertTrue(parsed.blocked)
        self.assertEqual(parsed.total_rows, 2)

    def test_no_us_fallback(self):
        self.assertIsNone(parse_date("12/31/2026", CALENDAR_WINDOWS_1900, date(2025, 1, 1), date(2027, 12, 31))[0])

    def test_no_aplica_is_distinct_from_blank(self):
        values = row(**{name: "NO APLICA" for name in ("arl", "documento_notificacion_arl", "estado_arl",
                       "fecha_notificacion_arl", "fecha_envio_arl", "guia_arl", "fecha_recibido_arl")})
        parsed = parse_workbook(excel([values]), profile())
        self.assertFalse(parsed.blocked)
        self.assertEqual(parsed.rows[0].masks["arl"], 127)
        self.assertNotIn("eps", parsed.rows[0].masks)

    def test_formula_without_cached_value_blocks(self):
        parsed = parse_workbook(excel([row(estado_arl='="Se notifico"')]), profile())
        self.assertTrue(parsed.blocked)
        self.assertIn("FORMULA_SIN_CACHE", {i.codigo for i in parsed.issues})

    def test_leading_zeros_survive(self):
        parsed = parse_workbook(excel(), profile())
        self.assertEqual(parsed.rows[0].values["identificacion_paciente"], "001234")
        parsed = parse_workbook(excel([row(identificacion_paciente=1234)], formats={"D2": "000000"}), profile())
        self.assertEqual(parsed.rows[0].values["identificacion_paciente"], "001234")

    def test_long_identifier_and_long_text_are_not_truncated(self):
        for values in (row(identificacion_paciente=1234567890123456), row(nombre_paciente="X" * 501)):
            self.assertTrue(parse_workbook(excel([values]), profile()).blocked)

    def test_empty_file_and_row_limit(self):
        self.assertTrue(parse_workbook(excel([{}]), profile()).blocked)
        with self.assertRaises(AppError) as caught:
            parse_workbook(excel([row(), row()]), profile(), max_rows=1)
        self.assertEqual(caught.exception.status, 413)


class PayloadTests(unittest.TestCase):
    def test_profile_cannot_weaken_required_sql_keys(self):
        specs = tuple(replace(f, obligatorio=False) if f.campo == "fecha_audiencia" else f for f in profile().fields)
        with self.assertRaises(AppError):
            replace(profile(), fields=specs).validate()

    def test_utc_and_seven_decimals(self):
        result = payload(fecha_modificacion_origen="2026-10-26T10:30:00.1234567-05:00")
        self.assertEqual(result.fecha_modificacion_origen, "2026-10-26T15:30:00.1234567")

    def test_invalid_base64_and_metadata(self):
        for changes in ({"contenido_base64": "%%%"}, {"sala": True}, {"id_perfil": 0},
                        {"fecha_modificacion_origen": "2026-10-26T10:30:00"}, {"nombre_archivo": "control.xls"}):
            with self.subTest(changes=changes), self.assertRaises(AppError):
                payload(**changes)

    def test_hash_is_of_binary_not_base64(self):
        result = payload()
        self.assertEqual(result.content_hash, hashlib.sha256(result.content).digest())


class Cursor:
    def __init__(self):
        self.calls = []
    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return self
    def close(self):
        pass


class Connection:
    def __init__(self):
        self.test_cursor = Cursor()
    def cursor(self):
        return self.test_cursor
    def close(self):
        pass


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = Connection()
        self.repo = SqlRepository(self.connection, time.monotonic() + 10)

    def test_transaction_commit_and_failure_rollback(self):
        with self.repo.transaction():
            self.repo.execute("INSERT prueba VALUES(?);", ("dato",))
        self.assertEqual(self.connection.test_cursor.calls[-1][0], "COMMIT TRANSACTION;")
        with self.assertRaises(ValueError):
            with self.repo.transaction():
                raise ValueError("fallo")
        self.assertIn("ROLLBACK TRANSACTION", self.connection.test_cursor.calls[-1][0])

    def test_rollback_still_runs_after_deadline(self):
        with self.assertRaises(AppError):
            with self.repo.transaction():
                self.repo.deadline = 0
        self.assertIn("ROLLBACK TRANSACTION", self.connection.test_cursor.calls[-1][0])

    def test_entity_computed_hash_is_not_inserted_and_values_are_parameters(self):
        name = "ENTIDAD '); DROP TABLE core.caso;--"
        with patch.object(self.repo, "one", side_effect=[None, {"id_entidad": 42}]) as query:
            self.assertEqual(self.repo._entity(name), 42)
        lookup, insertion = query.call_args_list
        self.assertIn("hash_nombre", lookup.args[0])
        self.assertNotIn("hash_nombre", insertion.args[0])
        self.assertNotIn(name, insertion.args[0])
        self.assertEqual(insertion.args[1], (name,))

    def test_hash_collision_is_rejected(self):
        expected = {"id_caso": 7, "hash_datos": b"1" * 32}
        with patch.object(self.repo, "one", return_value=dict(id_caso_dato=2, nombre_paciente="OTRO", **expected)):
            with self.assertRaises(AppError) as caught:
                self.repo.ensure("core.caso_dato", expected, {"nombre_paciente": "UNO"})
        self.assertEqual(caught.exception.code, "COLISION_DATOS")

    def test_identical_fact_reuses_id_and_local_cache(self):
        expected = {"id_caso": 7, "hash_datos": b"1" * 32}
        with patch.object(self.repo, "one", return_value=dict(id_caso_dato=2, nombre_paciente="UNO", **expected)) as query:
            for _ in range(2):
                self.assertEqual(self.repo.ensure("core.caso_dato", expected, {"nombre_paciente": "UNO"}), 2)
        self.assertEqual(query.call_count, 1)

    def test_sql_errors_are_classified_without_returning_source_values(self):
        error = Exception("40001", "[SQL Server]deadlock victim (1205) (SQLExecDirectW)")
        self.assertEqual(sql_number(error), 1205)
        self.assertTrue(retryable_sql(error))
        self.assertEqual(sql_number(Exception("42000", "(51207) (266)")), 51207)

    def test_published_load_does_not_insert_rows_again(self):
        data = payload()
        parsed = parse_workbook(data.content, profile())
        with patch.object(self.repo, "lock_file"), \
             patch.object(self.repo, "one", side_effect=[{"estado": "PUBLICADA"}, {"advertencias": 0}]), \
             patch.object(self.repo, "procedure", return_value={"cambio_carga_vigente": 0}), \
             patch.object(self.repo, "_insert_row") as insert:
            result = self.repo.ingest(data, 123, parsed)
        insert.assert_not_called()
        self.assertTrue(result["reutilizada"])

    def test_publish_failure_rolls_back_the_file_transaction(self):
        data = payload()
        parsed = parse_workbook(data.content, profile())
        with patch.object(self.repo, "lock_file"), \
             patch.object(self.repo, "one", side_effect=[{"estado": "RECIBIDA"}, {"cantidad": 0}]), \
             patch.object(self.repo, "_insert_row", return_value=321), \
             patch.object(self.repo, "procedure", side_effect=[None, None, Exception("42000", "(51207) (266)")]):
            with self.assertRaises(Exception):
                self.repo.ingest(data, 123, parsed)
        self.assertIn("ROLLBACK TRANSACTION", self.connection.test_cursor.calls[-1][0])
        self.assertNotIn("COMMIT TRANSACTION;", [c[0] for c in self.connection.test_cursor.calls])


class ServiceTests(unittest.TestCase):
    def test_invalid_required_row_never_enters_fact_writes(self):
        from sabana.service import consolidate
        data = payload(excel([row(identificacion_paciente=None)]))
        with patch("sabana.service.SqlRepository") as repository:
            instance = repository.return_value
            instance.profile.return_value = profile()
            instance.register.return_value = (1, 2, True)
            result, status = consolidate(data, connection_factory=lambda: object())
        self.assertEqual(status, 422)
        self.assertEqual(result["filas_procesadas"], 0)
        instance.ingest.assert_not_called()
        instance.record_failure.assert_called_once()

    def test_stale_event_is_a_conflict_even_with_secondary_sql_error(self):
        from sabana.service import consolidate
        with patch("sabana.service.SqlRepository") as repository:
            instance = repository.return_value
            instance.profile.return_value = profile()
            instance.register.return_value = (1, 2, False)
            instance.ingest.side_effect = Exception("42000", "(51207) (266)")
            with self.assertRaises(AppError) as caught:
                consolidate(payload(), connection_factory=lambda: object())
        self.assertEqual(caught.exception.status, 409)


class HttpTests(unittest.TestCase):
    def setUp(self):
        import azure.functions as func
        import function_app
        self.func, self.entry = func, function_app

    def request(self, document):
        return self.func.HttpRequest(method="POST", url="https://test.invalid/api/consolidar-sabana",
                                     body=json.dumps(document).encode("utf-8"))

    def test_azure_functions_can_index_the_app(self):
        functions = self.entry.app.get_functions()
        names = {f.get_function_name() for f in functions}
        self.assertEqual(len(names), 10)
        self.assertIn("ConsolidarSabana", names)
        self.assertIn("IniciarIngestaPst", names)
        self.assertIn("OrquestarPst", names)

    def test_invalid_request_returns_400_without_sql(self):
        response = self.entry.consolidar_sabana(self.request({}))
        self.assertEqual(response.status_code, 400)
        self.assertIn("correlacion", json.loads(response.get_body()))

    def test_success_and_sql_unavailable_response(self):
        p = payload()
        document = {k: getattr(p, k) for k in ("drive_id", "item_id", "nombre_archivo", "ruta_sharepoint", "version_origen", "sala", "id_perfil")}
        document.update(fecha_modificacion_origen="2026-10-26T15:30:00Z", contenido_base64=base64.b64encode(p.content).decode("ascii"))
        with patch.object(self.entry, "consolidate", return_value=({"estado": "PUBLICADA", "id_carga": 1}, 200)):
            self.assertEqual(self.entry.consolidar_sabana(self.request(document)).status_code, 200)
        with patch.object(self.entry, "consolidate", side_effect=AppError("SQL_NO_DISPONIBLE", "Reintente.", 503)):
            result = self.entry.consolidar_sabana(self.request(document))
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.headers["Retry-After"], "10")


if __name__ == "__main__":
    unittest.main()
