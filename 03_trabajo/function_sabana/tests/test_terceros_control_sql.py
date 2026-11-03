"""Opt-in: DDL/control/publicacion probados en transacciones revertidas."""
import copy
import gzip
import json
import os
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch

from terceros import control,repository
from terceros.automation_domain import AutomationError
from terceros.domain import digest


@unittest.skipUnless(os.environ.get('TERCEROS_CONTROL_SQL_INSTANTANEA'), 'Requiere identidad y snapshot SQL explicitos')
class ControlSqlTests(unittest.TestCase):
    def setUp(self):
        self.connection=repository.connect('JRB',write=True)
        self.cursor=self.connection.cursor()
        self.original=tuple(self.cursor.execute("SELECT (SELECT id_carga_terceros FROM ing.terceros_publicacion WHERE id=1),"
                         "(SELECT COUNT_BIG(*) FROM ing.terceros_carga),OBJECT_ID(N'ing.terceros_actualizacion',N'U');").fetchone())
        if self.original[2] is not None and self.cursor.execute('SELECT COUNT_BIG(*) FROM ing.terceros_actualizacion;').fetchone()[0]:
            self.connection.close()
            self.skipTest('La cola contiene solicitudes reales; no se prueban reservas globales sobre ellas')
        self.cursor.execute(repository.SESSION_OPTIONS)
        self.cursor.execute('BEGIN TRANSACTION;')
        path=Path(__file__).resolve().parents[1]/'sql'/'12_control_actualizacion_terceros.sql'
        self.cursor.execute(path.read_text(encoding='utf-8'))
        while self.cursor.nextset():pass
        # Todas las funciones usan esta conexion y transacciones anidadas.
        # El COMMIT de una funcion nunca confirma la transaccion externa de la prueba.
        inner=self.connection
        class Proxy:
            def cursor(self):return inner.cursor()
            def close(self):pass
        self.patchers=[patch.object(control,'connect',return_value=Proxy()),
                       patch.object(repository,'connect',return_value=Proxy()),
                       patch.dict(os.environ,{'TERCEROS_DEBOUNCE_SECONDS':'0'})]
        for p in self.patchers:p.start()
        self.notice=dict(origen='JNC',tipo_fuente='LOTE',id_carga_origen='PRUEBA_CONTROL_SQL',hash_revision='ab'*32)

    def tearDown(self):
        for p in reversed(self.patchers):p.stop()
        try:
            self.cursor.execute('IF @@TRANCOUNT>0 ROLLBACK TRANSACTION;')
            after=tuple(self.cursor.execute("SELECT (SELECT id_carga_terceros FROM ing.terceros_publicacion WHERE id=1),"
                       "(SELECT COUNT_BIG(*) FROM ing.terceros_carga),OBJECT_ID(N'ing.terceros_actualizacion',N'U');").fetchone())
            self.assertEqual(after,self.original)
        finally:self.connection.close()

    def register(self,revision='ab'):
        return control.register(dict(self.notice,hash_revision=revision*32))['id_actualizacion']

    def expire(self,reservation):
        self.cursor.execute("UPDATE ing.terceros_actualizacion SET creado_utc=DATEADD(MINUTE,-10,SYSUTCDATETIME()),"
          "inicio_intento_utc=DATEADD(MINUTE,-5,SYSUTCDATETIME()),reserva_hasta_utc=DATEADD(SECOND,-1,SYSUTCDATETIME()),"
          "actualizado_utc=SYSUTCDATETIME() WHERE id_ejecucion=?;",reservation['id_ejecucion'])

    def test_deduplication_grouping_retry_expiration_and_fencing(self):
        first=self.register();self.assertEqual(self.register(),first)
        second=self.register('cd')
        batch=control.claim();self.assertEqual(batch['solicitudes'],2)
        self.assertEqual(control.claim()['estado'],'ESPERANDO')
        third=self.register('ef')
        control.heartbeat(batch)
        self.expire(batch)
        replacement=control.claim()
        self.assertNotEqual(batch['id_ejecucion'],replacement['id_ejecucion'])
        self.assertEqual(replacement['solicitudes'],3)
        # Esta comprobacion no abre transaccion propia; no invalida la prueba externa.
        with self.assertRaisesRegex(AutomationError,'TERCEROS_RESERVA_PERDIDA'):
            control.assert_reserved(self.cursor,batch)
        control.fail(batch)  # Un trabajador antiguo tampoco puede marcar fallo al nuevo.
        self.assertEqual(control.state(first)['estado'],'EN_PROCESO')
        control.fail(replacement)
        self.assertEqual(control.state(first)['estado'],'REINTENTO')
        self.assertEqual(control.state(third)['estado'],'REINTENTO')

    def test_last_expired_attempt_is_exhausted(self):
        request=self.register()
        self.cursor.execute('UPDATE ing.terceros_actualizacion SET max_intentos=1 WHERE id_actualizacion=?;',request)
        batch=control.claim();self.expire(batch)
        self.assertEqual(control.claim()['estado'],'SIN_PENDIENTES')
        self.assertEqual(control.state(request)['estado'],'AGOTADA')

    def test_new_notice_extends_shared_window_but_duplicate_does_not(self):
        first=self.register()
        with patch.dict(os.environ,{'TERCEROS_DEBOUNCE_SECONDS':'600'}):
            second=self.register('cd')
            dates=list(self.cursor.execute('SELECT disponible_utc FROM ing.terceros_actualizacion WHERE id_actualizacion IN(?,?) ORDER BY id_actualizacion;',first,second))
            self.assertEqual(dates[0][0],dates[1][0])
            self.register('cd')
            dates_after=list(self.cursor.execute('SELECT disponible_utc FROM ing.terceros_actualizacion WHERE id_actualizacion IN(?,?) ORDER BY id_actualizacion;',first,second))
            self.assertEqual(dates,dates_after)
        self.assertEqual(control.claim()['estado'],'ESPERANDO')

    def document(self):
        with gzip.open(os.environ['TERCEROS_CONTROL_SQL_INSTANTANEA'],'rt',encoding='utf-8') as f:document=json.load(f)
        document['capturado_utc']=datetime.now(timezone.utc).isoformat()
        return document

    def test_known_snapshot_completes_batch_atomically_and_keeps_new_notice_pending(self):
        first=self.register();batch=control.claim();later=self.register('cd')
        result=repository.apply(self.document(),reservation=batch)
        self.assertTrue(result['reutilizada'])
        self.assertEqual(control.state(first)['estado'],'COMPLETADA')
        self.assertEqual(control.state(later)['estado'],'PENDIENTE')
        self.assertEqual(control.state(first)['id_carga_terceros'],result['id_carga_terceros'])

    def test_failure_after_publication_rolls_back_catalog_and_completion(self):
        request=self.register();batch=control.claim()
        document=copy.deepcopy(self.document())
        document['datos']['conteos']['PRUEBA_CONTROL_ATOMICIDAD']=1
        document['sha256']=digest(document['datos'])
        # Provocar el fallo justo despues de publicar, cuando todos los INSERT ya ocurrieron.
        real_complete=control.complete
        def fail(cursor,reservation,load):
            real_complete(cursor,reservation,load)
            raise RuntimeError('FALLO_CONTROLADO')
        with patch.object(control,'complete',side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,'FALLO_CONTROLADO'):
                repository.apply(document,reservation=batch)
        # apply revierte tambien la transaccion externa; tearDown verifica estado original.
        self.assertEqual(self.cursor.execute('SELECT @@TRANCOUNT;').fetchone()[0],0)


if __name__=='__main__':unittest.main()
