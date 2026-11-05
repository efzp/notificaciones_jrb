"""Pruebas opt-in: solo archivos sinteticos propios, siempre ROLLBACK exterior."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from canopus import domain, excel, repository
from terceros.database import connect
from test_canopus import payload, workbook, ROW


class BorrowedConnection:
    def __init__(self, connection):self.connection=connection
    def __getattr__(self, name):return getattr(self.connection,name)
    def close(self):pass


@unittest.skipUnless(os.getenv('CANOPUS_CARGA_SQL_VALIDAR')=='1','Requiere identidad y DDL15 aplicado')
class CanopusLoadSqlTests(unittest.TestCase):
    def setUp(self):
        self.connection=connect('JRB',write=True)
        self.addCleanup(self.cleanup)
        cursor=self.connection.cursor()
        if not cursor.execute("SELECT OBJECT_ID(N'ing.canopus_carga');").fetchone()[0]:
            self.skipTest('Aplicar primero SQL15 en la base de pruebas')
        self.before=self.totals()
        cursor.execute('BEGIN TRANSACTION;')
        self.connect_patch=patch.object(repository,'connect',return_value=BorrowedConnection(self.connection))
        self.connect_patch.start();self.addCleanup(self.connect_patch.stop)
        self.repo=repository.Repository()
        self.item='PRUEBA-'+uuid4().hex

    def totals(self):
        return tuple(self.connection.cursor().execute("""SELECT
            (SELECT COUNT_BIG(*) FROM ing.canopus_carga),
            (SELECT COUNT_BIG(*) FROM ing.canopus_fila),
            (SELECT COUNT_BIG(*) FROM ref.contacto_reportado),
            (SELECT COUNT_BIG(*) FROM mail.direccion),
            (SELECT id_carga_terceros FROM ing.terceros_publicacion WHERE id=1);""").fetchone())

    def cleanup(self):
        try:
            self.connection.cursor().execute('IF @@TRANCOUNT>0 ROLLBACK TRANSACTION;')
            if hasattr(self,'before'):self.assertEqual(self.totals(),self.before)
        finally:self.connection.close()

    def info(self,version=1,content=None):
        document=payload(content)
        document.update(item_id=self.item,version_origen=str(version),
                        fecha_modificacion_origen=f'2026-11-04T10:{version:02d}:00Z')
        return domain.intake(document)[0]

    def load(self,info,content=None):
        load=self.repo.register(info)
        rows,counts=excel.parse(content or workbook())
        return self.repo.publish(load,info,rows,counts)

    def current(self):
        return self.connection.cursor().execute('SELECT id_carga_canopus FROM ing.canopus_carga WHERE item_id=? AND vigente=1;',self.item).fetchone()[0]

    def test_repeated_activity_reuses_rows_and_addresses(self):
        content=workbook();info=self.info(content=content)
        first=self.load(info,content);before=self.totals()
        second=self.load(info,content)
        self.assertEqual(first['id_carga_canopus'],second['id_carga_canopus'])
        self.assertTrue(second['contenido_reutilizado'])
        self.assertEqual(before,self.totals())
        cursor=self.connection.cursor()
        self.assertEqual(cursor.execute('SELECT COUNT(*) FROM ref.contacto_reportado c JOIN ing.canopus_fila f ON f.id_fila_canopus=c.id_fila_canopus WHERE f.id_carga_canopus=?;',first['id_carga_canopus']).fetchone()[0],5)

    def test_versions_cannot_replace_newer_and_prior_content_can_be_reused(self):
        a=workbook();brow=list(ROW);brow[1]='nuevo@example.invalid';b=workbook([brow])
        first=self.load(self.info(1,a),a)
        second=self.load(self.info(2,b),b)
        self.assertNotEqual(first['id_carga_canopus'],second['id_carga_canopus'])
        with self.assertRaisesRegex(domain.CanopusError,'VERSION_ANTIGUA'):
            self.load(self.info(1,a),a)
        self.assertEqual(self.current(),second['id_carga_canopus'])
        with self.assertRaisesRegex(domain.CanopusError,'VERSION_CONFLICTIVA'):
            self.load(self.info(2,a),a)
        third=self.load(self.info(3,a),a)
        self.assertEqual(third['id_carga_canopus'],first['id_carga_canopus'])
        self.assertTrue(third['contenido_reutilizado'])
        self.assertEqual(self.current(),first['id_carga_canopus'])

    def test_failure_after_rows_and_directions_rolls_back_preserving_current(self):
        a=workbook();first=self.load(self.info(1,a),a)
        row=list(ROW);row[1]='rollback-'+uuid4().hex+'@example.invalid';b=workbook([row])
        info=self.info(2,b);load=self.repo.register(info)
        rows,counts=excel.parse(b)
        before=self.totals();original=repository.bulk
        def fail_contacts(cursor,sql,values,sizes):
            if 'INSERT ref.contacto_reportado' in sql:raise RuntimeError('PRUEBA_FALLO')
            return original(cursor,sql,values,sizes)
        with patch.object(repository,'bulk',side_effect=fail_contacts), self.assertRaisesRegex(RuntimeError,'PRUEBA_FALLO'):
            self.repo.publish(load,info,rows,counts)
        self.assertEqual(self.totals(),before)
        self.assertEqual(self.current(),first['id_carga_canopus'])
        self.repo.mark_error(load)
        self.repo.mark_error(first['id_carga_canopus'])
        self.assertEqual(self.connection.cursor().execute('SELECT estado FROM ing.canopus_carga WHERE id_carga_canopus=?;',first['id_carga_canopus']).fetchone()[0],'PUBLICADA')
        self.assertEqual(self.connection.cursor().execute('SELECT estado FROM ing.canopus_carga WHERE id_carga_canopus=?;',load).fetchone()[0],'ERROR')

    @unittest.skipUnless(os.getenv('CANOPUS_ARCHIVO_REAL_VALIDAR')=='1','Validacion adicional del Excel local completo')
    def test_real_workbook_publishes_all_rows_then_rolls_back(self):
        path=next((Path(__file__).resolve().parents[3]/'canopus').glob('*.XLSX'))
        content=path.read_bytes();info=self.info(1,content)
        rows,counts=excel.parse(content,self.repo.matcher())
        load=self.repo.register(info)
        result=self.repo.publish(load,info,rows,counts)
        self.assertEqual(result['filas'],2750)
        self.assertEqual(result['contactos'],7124)
        cursor=self.connection.cursor()
        self.assertEqual(cursor.execute('SELECT COUNT(*) FROM ing.canopus_fila WHERE id_carga_canopus=?;',load).fetchone()[0],2750)
        self.assertEqual(cursor.execute('SELECT COUNT(*) FROM ref.contacto_reportado c JOIN ing.canopus_fila f ON f.id_fila_canopus=c.id_fila_canopus WHERE f.id_carga_canopus=?;',load).fetchone()[0],7124)
