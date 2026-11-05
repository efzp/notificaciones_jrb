import asyncio
import base64
import io
import json
import os
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import azure.functions as func
from openpyxl import Workbook

from canopus import domain, excel, functions, service, storage
from terceros.matching import EntityMatcher

HEADERS = ['identificacion', 'correo_electronico', 'ultimo_empleador', 'email_empresa',
           'correo_electronicofe', 'fecha_audiencia', 'reparto', 'pcl']
ROW = ['00123456', 'A@EXAMPLE.COM; b@example.com; invalido', 'Empresa Prueba SAS',
       'nomina@example.com', 'fe@example.com', '03/06/2026', 'R1', '=1+1']
META = dict(drive_id=domain.DRIVE, item_id='ITEM123', nombre_archivo='CANOPUS.XLSX',
            version_origen='v1', fecha_modificacion_origen='2026-11-04T10:00:00Z')


def workbook(rows=None, headers=None):
    book = Workbook()
    book.active.append(headers or HEADERS)
    for row in [ROW] if rows is None else rows:
        book.active.append(row)
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


def payload(content=None, **changes):
    return dict(META, contenido_base64=base64.b64encode(content or workbook()).decode(), **changes)


class DomainTests(unittest.TestCase):
    def test_stable_identity_includes_revision_and_normalizes_utc(self):
        document = payload()
        info, content = domain.intake(document)
        self.assertEqual(info, domain.intake(document)[0])
        equivalent = dict(document, fecha_modificacion_origen='2026-11-04T05:00:00-05:00')
        self.assertEqual(info, domain.intake(equivalent)[0])
        self.assertNotEqual(info['id_seguimiento'], domain.intake(dict(document,version_origen='v2'))[0]['id_seguimiento'])
        self.assertNotIn('contenido_base64', info)
        self.assertEqual(content, base64.b64decode(document['contenido_base64']))

    def test_rejects_invalid_metadata_untrusted_paths_unknown_fields_and_base64(self):
        document = payload()
        for change in ({'drive_id':'otro'}, {'item_id':'https://example.invalid/a'},
                       {'nombre_archivo':'../x.xlsx'}, {'nombre_archivo':'x.xlsm'},
                       {'contenido_base64':'bad%base64'}, {'fecha_modificacion_origen':'2026-11-04'},
                       {'extra':'private'}, {'version_origen':' x '}, {'item_id':12}):
            with self.subTest(change=change), self.assertRaises(domain.CanopusError):
                domain.intake(dict(document, **change))
        with patch.dict(os.environ, {'CANOPUS_MAX_BYTES':'10'}), self.assertRaises(domain.CanopusError):
            domain.intake(document)


class ParserTests(unittest.TestCase):
    def matcher(self):
        return EntityMatcher([dict(id_entidad=10,nombre='Empresa Prueba SAS')],
                             [dict(id_entidad=10,tipo_parte='ultimoempleador')])

    def test_multiple_contacts_mixed_invalid_and_unclassified_fe_no_clinical_copy(self):
        rows, counts = excel.parse(workbook(), self.matcher())
        row = rows[0]
        self.assertEqual(row['cedula'], '00123456')
        self.assertEqual(row['fecha'],date(2026,6,3))
        self.assertEqual(row['entidad'],10)
        self.assertNotIn('pcl',row)
        self.assertEqual(counts['contactos'],5)
        self.assertEqual(counts['contactos_revision'],2)
        self.assertEqual({c['correo'] for c in row['contactos'] if c['estado']=='UTILIZABLE'},
                         {'A@example.com','b@example.com','nomina@example.com'})
        self.assertEqual(next(c for c in row['contactos'] if c['rol']=='por_definir')['estado'],'REVISION')
        self.assertTrue(any(c['correo'] is None and c['codigo']=='CANOPUS_CORREO_INVALIDO' for c in row['contactos']))

    def test_unknown_employer_preserves_patient_and_employer_for_review(self):
        row = list(ROW);row[2]='Organizacion Desconocida'
        rows, counts = excel.parse(workbook([row]),self.matcher())
        self.assertEqual(rows[0]['estado_empleador'],'SIN_ENTIDAD')
        self.assertIsNone(rows[0]['entidad'])
        self.assertTrue(all(c['estado']=='REVISION' for c in rows[0]['contactos'] if c['rol']=='ultimoempleador'))
        self.assertTrue(any(c['estado']=='UTILIZABLE' for c in rows[0]['contactos'] if c['rol']=='paciente'))

    def test_rejects_missing_duplicate_headers_blank_id_and_formula_input(self):
        for content in (workbook(headers=['otra']),workbook(headers=HEADERS+['identificacion']),
                        workbook([[None]+ROW[1:]]),workbook([['=123']+ROW[1:]]), b'not xlsx',workbook([])):
            with self.subTest(size=len(content)), self.assertRaises(domain.CanopusError):
                excel.parse(content)

    def test_limits_prevent_partial_or_silent_load(self):
        with patch.dict(os.environ,{'CANOPUS_MAX_ROWS':'1'}), self.assertRaisesRegex(domain.CanopusError,'FILAS_LIMITE'):
            excel.parse(workbook([ROW,ROW]))
        with patch.dict(os.environ,{'CANOPUS_MAX_CONTACTS':'1'}), self.assertRaisesRegex(domain.CanopusError,'CONTACTOS_LIMITE'):
            excel.parse(workbook())

    def test_identifier_and_date_variants_do_not_round_or_remove_leading_zero(self):
        self.assertEqual(excel.identification(123456.0),'123456')
        self.assertEqual(excel.identification('00123456'),'00123456')
        for bad in (1.2,10**15,float('nan'),True,'1.234','1e6'):
            with self.assertRaises(domain.CanopusError):excel.identification(bad)
        epoch=datetime(1899,12,30)
        for value in ('2026-06-03','03/06/2026','03-06-2026',datetime(2026,6,3),46176):
            self.assertEqual(excel.reported_date(value,epoch)[0],date(2026,6,3))
        self.assertEqual(excel.reported_date('no fecha',epoch),(None,True))


async def invoke(builder, req, client):
    return await builder._function.get_user_function().__wrapped__(req,client)


@patch.dict(os.environ, {'CANOPUS_INGEST_ENABLED':'1'})
class HttpTests(unittest.TestCase):
    def request(self, body=None, job=None):
        return func.HttpRequest(method='POST', url='https://test.invalid/api/canopus/cargas?code=secret',
                                body=json.dumps(body or payload()).encode(),route_params={'job_id':job} if job else {})

    def client(self, state=None):
        current=SimpleNamespace(runtime_status=SimpleNamespace(name=state),output='private exception') if state else None
        return SimpleNamespace(get_status=AsyncMock(return_value=current),start_new=AsyncMock(),restart=AsyncMock())

    def test_202_stages_before_launch_without_parse_sql_or_base64_in_history(self):
        client=self.client()
        with patch.object(storage,'stage',side_effect=lambda info,content:dict(info,blob='technical',etag='etag')) as stage, \
             patch.object(excel,'parse') as parse, patch.object(service,'Repository') as repository:
            result=asyncio.run(invoke(functions.start,self.request(),client))
        self.assertEqual(result.status_code,202)
        stage.assert_called_once();parse.assert_not_called();repository.assert_not_called()
        document=client.start_new.call_args.kwargs['client_input']
        self.assertNotIn('contenido_base64',document)
        self.assertNotIn('secret',result.get_body().decode())
        self.assertNotIn('secret',result.headers['Location'])

    def test_duplicate_does_not_stage_or_restart_and_bad_input_does_not_schedule(self):
        client=self.client('Running')
        with patch.object(storage,'stage') as stage:
            self.assertEqual(asyncio.run(invoke(functions.start,self.request(),client)).status_code,202)
        stage.assert_not_called();client.start_new.assert_not_awaited()
        self.assertEqual(asyncio.run(invoke(functions.start,self.request({'x':'y'}),client)).status_code,400)

    def test_disabled_and_failed_storage_do_not_launch(self):
        client=self.client()
        with patch.dict(os.environ,{'CANOPUS_INGEST_ENABLED':'0'}):
            self.assertEqual(asyncio.run(invoke(functions.start,self.request(),client)).status_code,503)
        with patch.object(storage,'stage',side_effect=RuntimeError('private token')):
            result=asyncio.run(invoke(functions.start,self.request(),client))
        self.assertEqual(result.status_code,503)
        self.assertNotIn('private',result.get_body().decode())
        client.start_new.assert_not_awaited()

    def test_status_and_resume_do_not_echo_failed_exceptions(self):
        job=domain.intake(payload())[0]['id_seguimiento'];client=self.client('Failed')
        result=asyncio.run(invoke(functions.status,self.request(job=job),client))
        self.assertNotIn('private',result.get_body().decode())
        self.assertEqual(asyncio.run(invoke(functions.resume,self.request(job=job),client)).status_code,202)
        client.restart.assert_awaited_once()
        self.assertEqual(asyncio.run(invoke(functions.resume,self.request(job=job),self.client('Running'))).status_code,409)


@patch.dict(os.environ, {'CANOPUS_INGEST_ENABLED':'1'})
class ServiceTests(unittest.TestCase):
    def test_validation_errors_are_safe_and_previous_publication_is_not_marked_failed(self):
        repository=Mock();repository.register.return_value=7
        with patch.object(storage,'download',return_value=b'bad'),patch.object(service,'Repository',return_value=repository):
            result=service.process({'version_parser':domain.VERSION})
        self.assertEqual(result,dict(estado='ERROR',codigo='CANOPUS_XLSX_INVALIDO',id_carga_canopus=7))
        repository.publish.assert_not_called();repository.mark_error.assert_called_once_with(7)
        repository.close.assert_called_once()

    def test_infrastructure_errors_rethrow_without_private_sql(self):
        with patch.object(storage,'download',side_effect=RuntimeError('private token')), \
             self.assertRaisesRegex(RuntimeError,'^CANOPUS_ACTIVIDAD_NO_DISPONIBLE$'):
            service.process({'version_parser':domain.VERSION})

    def test_orchestrator_persists_only_reference_and_safe_result(self):
        reference={'blob':'technical.xlsx','etag':'etag'}
        context=SimpleNamespace(get_input=lambda:reference,set_custom_status=Mock(),
                                call_activity_with_retry=lambda name,retry,value:(name,value))
        flow=functions.run_orchestration(context)
        self.assertEqual(next(flow),('CanopusProcesar',reference))
        with self.assertRaises(StopIteration) as done:
            flow.send(dict(estado='PUBLICADA',filas=2))
        self.assertEqual(done.exception.value['filas'],2)


class StorageTests(unittest.TestCase):
    def test_blob_etag_and_hash_are_validated_before_parse(self):
        info,content=domain.intake(payload())
        info.update(blob=info['id_seguimiento']+'.xlsx',etag='etag')
        blob=Mock();blob.get_blob_properties.return_value=SimpleNamespace(size=len(content),etag='etag')
        blob.download_blob.return_value.readall.return_value=content
        client=Mock();client.get_blob_client.return_value=blob
        from contextlib import nullcontext
        with patch.object(storage,'container',return_value=nullcontext(client)):
            self.assertEqual(storage.download(info),content)
            blob.get_blob_properties.return_value.etag='changed'
            with self.assertRaisesRegex(domain.CanopusError,'BLOB_MODIFICADO'):storage.download(info)
        self.assertEqual(blob.download_blob.call_count,1)
