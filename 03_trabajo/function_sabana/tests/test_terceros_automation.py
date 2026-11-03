import asyncio
import json
import os
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import azure.functions as func

from terceros import automation, control
from terceros.automation_domain import AutomationError, Notice, execution
from terceros.functions import start, status, resume, recovery, run_orchestration, safe_activity

NOTICE = dict(origen="JRB", tipo_fuente="EXCEL", id_carga_origen="123", hash_revision="ab"*32)
RESERVATION = dict(estado="RESERVADA", id_ejecucion="c5e1b7e4-3e43-4cc1-9d5c-9aa03680981a", solicitudes=2)


async def invoke(builder, req, client):
    return await builder._function.get_user_function().__wrapped__(req, client)


class NoticeTests(unittest.TestCase):
    def test_idempotent_job_and_correction_creates_another_job(self):
        a = Notice.parse(NOTICE)
        self.assertEqual(a.job_id, Notice.parse(dict(NOTICE, hash_revision=NOTICE['hash_revision'].upper())).job_id)
        self.assertNotEqual(a.job_id, Notice.parse(dict(NOTICE, hash_revision='cd'*32)).job_id)

    def test_rejects_patient_data_urls_unknown_fields_and_invalid_origins(self):
        for change in ({'paciente': 'private'}, {'contenido_base64': 'AAAA'}, {'id_carga_origen': 'https://x'},
                       {'tipo_fuente': 'LOTE'}, {'origen': 'JNC'}, {'hash_revision': 'etag'},
                       {'id_carga_origen': 123}, {'id_carga_origen': ' x '}, {'hash_revision': 'x'*64}):
            with self.subTest(change=change), self.assertRaises(AutomationError):
                Notice.parse(dict(NOTICE, **change))
        for value in (None, [], 'text'):
            with self.assertRaises(AutomationError):Notice.parse(value)

    def test_execution_token_requires_guid_and_positive_bounded_count(self):
        self.assertEqual(execution(RESERVATION)[1], 2)
        for value in (dict(RESERVATION, solicitudes=True), dict(RESERVATION, solicitudes=0),
                      dict(RESERVATION, solicitudes=1001),dict(RESERVATION,id_ejecucion='invalid')):
            with self.assertRaises(AutomationError):execution(value)


@patch.dict(os.environ, {'TERCEROS_AUTOMATION_ENABLED': '1'})
class HttpTests(unittest.TestCase):
    def request(self, body=NOTICE, method='POST', job_id=None):
        return func.HttpRequest(method=method,url='https://test.invalid/api/terceros/actualizaciones?code=secret',
                                body=json.dumps(body).encode(),route_params={'job_id':job_id} if job_id else {})

    def client(self, state=None):
        current = SimpleNamespace(runtime_status=SimpleNamespace(name=state),output='private') if state else None
        return SimpleNamespace(get_status=AsyncMock(return_value=current),start_new=AsyncMock(),restart=AsyncMock())

    def test_202_does_not_wait_for_sql_or_capture_and_never_echoes_key(self):
        client = self.client()
        with patch.object(control,'register') as register, patch.object(automation,'capture') as capture:
            result=asyncio.run(invoke(start,self.request(),client))
        self.assertEqual(result.status_code,202)
        self.assertNotIn('secret',result.get_body().decode())
        self.assertNotIn('secret',result.headers['Location'])
        register.assert_not_called();capture.assert_not_called()
        client.start_new.assert_awaited_once()

    def test_duplicate_running_job_does_not_restart(self):
        client=self.client('Running')
        result=asyncio.run(invoke(start,self.request(),client))
        self.assertEqual(result.status_code,202)
        client.start_new.assert_not_awaited()

    def test_invalid_input_and_disabled_automation_never_schedule(self):
        client=self.client()
        result=asyncio.run(invoke(start,self.request({'x':'y'}),client))
        self.assertEqual(result.status_code,400)
        with patch.dict(os.environ,{'TERCEROS_AUTOMATION_ENABLED':'0'}):
            result=asyncio.run(invoke(start,self.request(),client))
        self.assertEqual(result.status_code,503)
        client.start_new.assert_not_awaited()

    def test_failed_status_and_activity_do_not_expose_private_errors(self):
        client=self.client('Failed')
        result=asyncio.run(invoke(status,self.request(method='GET',job_id=Notice.parse(NOTICE).job_id),client))
        self.assertNotIn('private',result.get_body().decode())
        with self.assertRaisesRegex(RuntimeError,'^TERCEROS_ACTIVIDAD_FALLIDA$'):
            safe_activity(Mock(side_effect=Exception('private SQL token')), {})

    def test_resume_is_restricted_to_failed_or_terminated_jobs(self):
        client=self.client('Running');req=self.request(job_id=Notice.parse(NOTICE).job_id)
        self.assertEqual(asyncio.run(invoke(resume,req,client)).status_code,409)
        client.restart.assert_not_awaited()
        client=self.client('Failed')
        self.assertEqual(asyncio.run(invoke(resume,req,client)).status_code,202)
        client.restart.assert_awaited_once()

    def test_disabled_recovery_does_not_access_sql_or_durable(self):
        client=self.client()
        with patch.dict(os.environ,{'TERCEROS_AUTOMATION_ENABLED':'0'}):
            asyncio.run(invoke(recovery,Mock(),client))
        client.get_status.assert_not_awaited()


class OrchestrationTests(unittest.TestCase):
    def context(self,value):
        return SimpleNamespace(get_input=lambda:value,current_utc_datetime=datetime(2026,11,3,tzinfo=timezone.utc),
            call_activity_with_retry=lambda name,retry,value:(name,value),create_timer=lambda deadline:('timer',deadline),
            set_custom_status=Mock(),continue_as_new=Mock())

    def test_register_wait_and_complete_without_persisting_snapshot_in_history(self):
        context=self.context({'aviso':NOTICE});flow=run_orchestration(context)
        self.assertEqual(next(flow)[0],'TercerosRegistrarAviso')
        self.assertEqual(flow.send({'id_actualizacion':7})[1],{'id_actualizacion':7})
        self.assertEqual(flow.send({'estado':'ESPERANDO','espera_segundos':600})[0],'timer')
        self.assertEqual(flow.send(None)[0],'TercerosProcesarActualizacion')
        with self.assertRaises(StopIteration) as done:flow.send({'estado':'COMPLETADA','id_carga_terceros':4})
        self.assertEqual(done.exception.value['id_carga_terceros'],4)

    def test_recovery_drains_batches_until_no_pending_requests(self):
        flow=run_orchestration(self.context({'recuperar':True}))
        self.assertEqual(next(flow)[1],{'id_actualizacion':None})
        self.assertEqual(flow.send({'estado':'PROCESADA'})[0],'TercerosProcesarActualizacion')
        with self.assertRaises(StopIteration):flow.send({'estado':'SIN_PENDIENTES'})

    def test_long_history_continues_with_only_request_id(self):
        context=self.context({'id_actualizacion':7});flow=run_orchestration(context);next(flow)
        for _ in range(49):flow.send({'estado':'PROCESADA'})
        with self.assertRaises(StopIteration):flow.send({'estado':'PROCESADA'})
        context.continue_as_new.assert_called_once_with({'id_actualizacion':7,'recuperar':False})


class ActivityTests(unittest.TestCase):
    def test_committed_request_is_replayed_without_claiming_or_capturing(self):
        with patch.object(control,'state',return_value={'estado':'COMPLETADA'}) as state, \
             patch.object(control,'claim') as claim,patch.object(automation,'capture') as capture:
            automation.process({'id_actualizacion':1})
        claim.assert_not_called();capture.assert_not_called()

    def test_busy_batch_does_not_capture(self):
        with patch.object(control,'claim',return_value={'estado':'ESPERANDO','espera_segundos':300}), \
             patch.object(automation,'capture') as capture:
            result=automation.process({})
        self.assertEqual(result['espera_segundos'],300);capture.assert_not_called()

    def test_capture_failure_marks_retry_without_publishing(self):
        with patch.object(control,'claim',return_value=RESERVATION),patch.object(control,'fail') as fail, \
             patch.object(automation,'Lease',return_value=nullcontext(Mock())), \
             patch.object(automation,'capture',side_effect=RuntimeError('private')), \
             patch.object(automation.repository,'apply') as apply:
            result=automation.process({})
        fail.assert_called_once_with(RESERVATION,'TERCEROS_ACTUALIZACION_FALLIDA')
        apply.assert_not_called();self.assertEqual(result['estado'],'ESPERANDO')

    def test_success_renews_reservation_before_atomic_publication(self):
        calls=[]
        with patch.object(control,'claim',return_value=RESERVATION), \
             patch.object(control,'heartbeat',side_effect=lambda r:calls.append('renew')), \
             patch.object(automation,'Lease',return_value=nullcontext(Mock())), \
             patch.object(automation,'capture',return_value={'sha256':'safe'}), \
             patch.object(automation.repository,'apply',side_effect=lambda *a,**kw:(calls.append(kw['reservation']) or {'id_carga_terceros':4,'reutilizada':True})):
            result=automation.process({})
        self.assertEqual(calls,['renew',RESERVATION]);self.assertTrue(result['reutilizada'])

    def test_lost_reservation_during_capture_prevents_publication(self):
        with patch.object(control,'claim',return_value=RESERVATION),patch.object(control,'fail') as fail, \
             patch.object(control,'heartbeat',side_effect=AutomationError('TERCEROS_RESERVA_PERDIDA')), \
             patch.object(automation,'Lease',return_value=nullcontext(Mock())), \
             patch.object(automation,'capture',return_value={}),patch.object(automation.repository,'apply') as apply:
            automation.process({})
        apply.assert_not_called();fail.assert_called_once_with(RESERVATION,'TERCEROS_RESERVA_PERDIDA')


class PackagingTests(unittest.TestCase):
    def test_deployment_contains_worker_and_checksum_files_without_private_snapshots(self):
        from tools.preparar_paquete import SOURCE,prepare
        with tempfile.TemporaryDirectory(prefix='.validation-terceros-',dir=SOURCE) as folder:
            destination=Path(folder)/'deploy'
            files=prepare(destination)
            self.assertIn('terceros/functions.py',files)
            self.assertIn('sql/09_catalogo_terceros.sql',files)
            self.assertIn('sql/10_equivalencias_terceros.sql',files)
            self.assertFalse(any('resultados' in f or f.endswith('.gz') or '11_dominios' in f for f in files))


if __name__=='__main__':unittest.main()
