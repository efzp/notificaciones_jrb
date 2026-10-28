from __future__ import annotations

import asyncio
import io
import json
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import azure.functions as func

from pst.domain import PstFailure, PstRequest
from pst.extract import extract_message, parse_report, text, utc
from pst.functions import run_orchestration, safe_activity, start, status, resume
from pst.range_io import RangeReader
from pst.repository import PstRepository
from pst import service
from pst.cloud import Graph, open_pst


DOCUMENT = {"drive_id": "drive", "item_id": "item", "version_origen": '"etag,1"'}


async def invoke(builder, request, client):
    # La capa Durable transforma el binding JSON en cliente; aqui se inyecta un cliente de prueba.
    return await builder._function.get_user_function().__wrapped__(request, client)


class RequestTests(unittest.TestCase):
    def test_same_source_version_is_idempotent_and_changes_produce_a_new_job(self):
        first = PstRequest.parse(DOCUMENT, "drive")
        self.assertEqual(first.job_id, PstRequest.parse(dict(DOCUMENT), "drive").job_id)
        self.assertNotEqual(first.job_id, PstRequest.parse(dict(DOCUMENT, version_origen="v2"), "drive").job_id)

    def test_reject_base64_arbitrary_urls_and_other_drives(self):
        for value in (dict(DOCUMENT, contenido_base64="AAAA"), dict(DOCUMENT, drive_id="other"),
                      dict(DOCUMENT, item_id="../x"), dict(DOCUMENT, version_origen="")):
            with self.subTest(value=value), self.assertRaises(Exception):
                PstRequest.parse(value, "drive")


class RangeTests(unittest.TestCase):
    def reader(self, content=b"0123456789abcdefghij", blocks=2):
        reads = []
        def fetch(start, length):
            reads.append((start, length))
            return content[start:start+length]
        return RangeReader(len(content), fetch, 4, blocks), reads

    def test_cross_block_seek_end_and_eof(self):
        reader, reads = self.reader()
        reader.seek(3)
        self.assertEqual(reader.read(6), b"345678")
        reader.seek(-3, io.SEEK_END)
        self.assertEqual(reader.read(99), b"hij")
        self.assertEqual(reader.read(2), b"")
        reader.seek(999)
        self.assertEqual(reader.read(1), b"")
        self.assertTrue(all(length <= 4 for start, length in reads))

    def test_cache_reuses_ranges_and_stays_bounded(self):
        reader, reads = self.reader()
        self.assertEqual(reader.read(4), b"0123")
        reader.seek(0)
        self.assertEqual(reader.read(4), b"0123")
        self.assertEqual(len(reads), 1)
        reader.read(12)
        self.assertEqual(len(reader.cache), 2)

    def test_truncated_or_failed_remote_read_is_not_silently_accepted(self):
        reader = RangeReader(8, lambda start, length: b"", 4)
        with self.assertRaises(OSError):
            reader.read(4)
        self.assertTrue(reader.failed)

    def test_large_pst_is_not_loaded_with_a_single_read(self):
        reader = RangeReader(8*1024**3, lambda start, length: b"x"*length)
        with self.assertRaises(ValueError):
            reader.read()
        reader.seek(7*1024**3)
        self.assertEqual(reader.read(4), b"xxxx")

    def test_native_callback_io_error_remains_retryable_infrastructure_failure(self):
        container = Mock()
        container.get_blob_client.return_value.download_blob.side_effect = OSError("private URL")
        with self.assertRaises(PstFailure) as caught:
            with open_pst(container, {"blob":"test.pst","blob_etag":"etag","bytes":4096}):
                self.fail("No debe abrir un PST si la lectura remota falla")
        self.assertEqual(caught.exception.code,"PST_RANGO_NO_DISPONIBLE")


class CloudTests(unittest.TestCase):
    def test_download_url_cannot_be_supplied_for_an_arbitrary_host(self):
        graph = Graph(Mock())
        try:
            for url in ("http://tenant.sharepoint.com/file", "https://tenant.sharepoint.com.evil.invalid/file"):
                with self.subTest(url=url), self.assertRaises(PstFailure):
                    list(graph.chunks({"@microsoft.graph.downloadUrl":url,"size":10}))
        finally:
            graph.close()

    def test_short_download_is_not_accepted_as_a_complete_pst(self):
        graph = Graph(Mock())
        try:
            with patch("requests.Session") as session:
                download = session.return_value.__enter__.return_value
                response = download.get.return_value.__enter__.return_value
                response.status_code = 200
                response.iter_content.return_value = [b"ab"]
                with self.assertRaises(PstFailure) as caught:
                    list(graph.chunks({"@microsoft.graph.downloadUrl":"https://tenant.sharepoint.com/file","size":4}))
                self.assertEqual(caught.exception.code,"PST_DESCARGA_INCOMPLETA")
                self.assertNotIn("Authorization",download.get.call_args.kwargs.get("headers",{}))
        finally:
            graph.close()


class ReportTests(unittest.TestCase):
    def test_report_recipient_is_not_mistaken_for_original_affected_recipient(self):
        body = "Final-Recipient: rfc822; destinatario@example.invalid\r\nDisposition: automatic-action/MDN-sent-automatically; displayed\r\n"
        entries = [SimpleNamespace(entry_type=0x001a,value_type=0x1f,data_as_string="REPORT.IPM.Note.IPNRN"),
                   SimpleNamespace(entry_type=0x0e08,value_type=3,data_as_integer=2048),
                   SimpleNamespace(entry_type=0x1000,value_type=0x1f,data_as_string=body)]
        record = SimpleNamespace(number_of_entries=len(entries),get_entry=lambda index:entries[index])
        message = SimpleNamespace(get_record_set=lambda index:record,recipients=None,number_of_attachments=0,
                                  transport_headers="To: auditor@example.invalid\r\n",identifier=7,subject="Aviso",
                                  sender_name=None,client_submit_time=None,delivery_time=None)
        value = extract_message(message)
        self.assertEqual(len(value["reportes"]),1)
        self.assertIn("destinatario@example.invalid",value["reportes"][0]["destinatario_final"])
        self.assertNotIn("auditor@example.invalid",value["reportes"][0]["destinatario_final"])

    def test_delivery_and_read_report_fields_remain_observations(self):
        body = "Final-Recipient: rfc822; persona@example.invalid\r\nDisposition: manual-action/MDN-sent-manually; displayed\r\n"
        report = parse_report("", body)[0]
        self.assertEqual(report["tipo"], 2)
        self.assertIn("displayed", report["disposicion"])
        self.assertNotIn("leido", report)

    def test_human_text_and_subject_do_not_become_read_receipts(self):
        self.assertEqual(parse_report("Subject: Leido: aviso", "El mensaje fue leido"), [])

    def test_unicode_limit_is_sql_utf16_limit_and_never_truncates(self):
        self.assertEqual(text("ab", 2), "ab")
        with self.assertRaises(PstFailure):
            text("😀", 1)

    def test_dates_are_utc_and_missing_dates_stay_missing(self):
        self.assertIsNone(utc(None))
        self.assertEqual(utc("Wed, 28 Oct 2026 10:00:00 -0500"), datetime(2026,10,28,15,tzinfo=timezone.utc))


class HttpTests(unittest.TestCase):
    def request(self, document=None, method="POST", job_id=None):
        return func.HttpRequest(method=method, url="https://app.invalid/api/pst/ingestas",
                                route_params={"job_id": job_id} if job_id else {},
                                body=json.dumps(document or DOCUMENT).encode())

    @patch.dict("os.environ", {"PST_DRIVE_ID": "drive"})
    def test_start_returns_202_without_sql_graph_or_pst_download(self):
        client = SimpleNamespace(get_status=AsyncMock(return_value=None), start_new=AsyncMock())
        with patch("pst.service.prepare") as prepare, patch("pst.service.repository") as repository:
            result = asyncio.run(invoke(start, self.request(), client))
        self.assertEqual(result.status_code, 202)
        self.assertIn("Location", result.headers)
        self.assertIn("id_seguimiento", json.loads(result.get_body()))
        client.start_new.assert_awaited_once()
        prepare.assert_not_called()
        repository.assert_not_called()

    @patch.dict("os.environ", {"PST_DRIVE_ID": "drive"})
    def test_duplicate_request_returns_the_same_job_without_starting_it_again(self):
        client = SimpleNamespace(get_status=AsyncMock(return_value=SimpleNamespace(runtime_status=SimpleNamespace(name="Running"))), start_new=AsyncMock())
        result = asyncio.run(invoke(start, self.request(), client))
        self.assertEqual(json.loads(result.get_body())["id_seguimiento"], PstRequest(**DOCUMENT).job_id)
        client.start_new.assert_not_awaited()

    def test_status_does_not_expose_failure_details_or_instance_input(self):
        current = SimpleNamespace(runtime_status=SimpleNamespace(name="Failed"), custom_status={"fase":"EXTRAYENDO"},
                                  output="secret URL token and mail content", input_="private")
        client = SimpleNamespace(get_status=AsyncMock(return_value=current))
        result = asyncio.run(invoke(status, self.request(method="GET", job_id=PstRequest(**DOCUMENT).job_id), client))
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("secret", result.get_body().decode())
        self.assertNotIn("private", result.get_body().decode())

    def test_cannot_resume_an_active_or_successful_job(self):
        client = SimpleNamespace(get_status=AsyncMock(return_value=SimpleNamespace(runtime_status=SimpleNamespace(name="Running"))), restart=AsyncMock())
        result = asyncio.run(invoke(resume, self.request(job_id=PstRequest(**DOCUMENT).job_id), client))
        self.assertEqual(result.status_code, 409)
        client.restart.assert_not_awaited()

    def test_activity_does_not_leak_native_or_sql_errors(self):
        with self.assertRaisesRegex(RuntimeError, "^PST_ACTIVIDAD_FALLIDA$"):
            safe_activity(Mock(side_effect=Exception("private token and SQL values")), {})


class OrchestrationTests(unittest.TestCase):
    def context(self, document):
        return SimpleNamespace(get_input=lambda: document, set_custom_status=Mock(), continue_as_new=Mock(),
                               call_activity_with_retry=lambda name,retry,value: (name,value))

    def test_successful_flow_publishes_only_after_batches_finish(self):
        context = self.context(DOCUMENT)
        generator = run_orchestration(context)
        self.assertEqual(next(generator)[0], "PstPreparar")
        self.assertEqual(generator.send({"id_carga":1,"completa":False})[0], "PstInventariar")
        self.assertEqual(generator.send({})[0], "PstProcesarLote")
        self.assertEqual(generator.send({"terminado":True})[0], "PstFinalizar")
        with self.assertRaises(StopIteration) as stopped:
            generator.send({"estado":"COMPLETA"})
        self.assertEqual(stopped.exception.value, {"estado":"COMPLETA"})

    def test_long_history_is_replaced_with_a_small_reference(self):
        reference = {"id_carga":1,"completa":False,"blob":"hash.pst"}
        context = self.context({"_referencia":reference})
        generator = run_orchestration(context)
        next(generator)
        for index in range(49):
            generator.send({"terminado":False,"mensajes_extraidos":index})
        with self.assertRaises(StopIteration):
            generator.send({"terminado":False})
        context.continue_as_new.assert_called_once_with({"_referencia":reference})

    def test_infrastructure_failure_marks_sql_load_before_failing(self):
        context = self.context({"_referencia":{"id_carga":1,"completa":False}})
        generator = run_orchestration(context)
        next(generator)
        self.assertEqual(generator.throw(RuntimeError("PST_ACTIVIDAD_FALLIDA"))[0], "PstMarcarError")
        with self.assertRaisesRegex(RuntimeError, "PST_INGESTA_FALLIDA"):
            generator.send({})


class CheckpointTests(unittest.TestCase):
    def test_replayed_committed_batch_does_not_insert_again(self):
        repo = object.__new__(PstRepository)
        @contextmanager
        def transaction():
            yield
        repo.transaction = transaction
        repo.lock = Mock()
        repo.one = Mock(return_value={"siguiente_indice":50})
        repo.execute = Mock()
        repo.message = Mock()
        repo.progress = Mock(return_value={"mensajes_extraidos":50})
        result = repo.batch(1,{"id_carpeta":2},0,[{"estado":"EXTRAIDO","mensaje":{}}])
        self.assertEqual(result["mensajes_extraidos"],50)
        repo.execute.assert_not_called()
        repo.message.assert_not_called()

    def test_prepare_does_not_copy_a_previously_completed_source_again(self):
        repo = Mock()
        repo.known.return_value = {"id_carga":7,"estado":"COMPLETA"}
        @contextmanager
        def repository():
            yield repo
        with (patch.dict("os.environ", {"PST_DRIVE_ID":"drive"}), patch("pst.service.identity"),
              patch("pst.service.Graph") as graph, patch("pst.service.repository",repository), patch("pst.service.stage_file") as stage):
            graph.return_value.metadata.return_value = {}
            result = service.prepare(DOCUMENT)
        self.assertEqual(result,{"id_carga":7,"completa":True})
        stage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
