"""DDL Canopus y prioridades: pruebas opt-in en transaccion revertida."""
import os
import unittest
from pathlib import Path
from uuid import uuid4

from terceros.database import connect


@unittest.skipUnless(os.environ.get('CANOPUS_SQL_VALIDAR')=='1','Requiere identidad SQL explicita')
class CanopusModelSqlTests(unittest.TestCase):
    def setUp(self):
        self.connection=connect('JRB',write=True)
        self.closed=False
        self.addCleanup(self.cleanup_connection)
        self.cursor=self.connection.cursor()
        self.before=tuple(self.cursor.execute("SELECT OBJECT_ID(N'ing.canopus_carga',N'U'),"
           "OBJECT_ID(N'ing.canopus_fila',N'U'),OBJECT_ID(N'ref.contacto_reportado',N'U'),"
           "OBJECT_ID(N'rep.vw_contactos_caso',N'V'),"
           "(SELECT id_carga_terceros FROM ing.terceros_publicacion WHERE id=1);").fetchone())
        if any(v is not None for v in self.before[:4]):
            self.connection.close();self.closed=True
            self.skipTest('Existen objetos reales; validar en una copia antes de ejecutar estas pruebas')
        self.cursor.execute('BEGIN TRANSACTION;')
        ddl=(Path(__file__).resolve().parents[1]/'sql'/'15_contactos_canopus_consolidado.sql').read_text(encoding='utf-8')
        self.cursor.execute(ddl)
        while self.cursor.nextset():pass
        self.cursor.execute('SET XACT_ABORT OFF;')
        references={r[0] for r in self.cursor.execute("""SELECT DISTINCT ec.id_entidad
        FROM ing.terceros_publicacion p JOIN ref.entidad_correo_fuente ef ON ef.id_carga_terceros=p.id_carga_terceros
        JOIN ref.entidad_correo ec ON ec.id_entidad_correo=ef.id_entidad_correo WHERE ec.tipo_parte='ultimoempleador';""")}
        canonical={r[0]:r[1] for r in self.cursor.execute("""SELECT e.id_entidad_origen,e.id_entidad_representante
        FROM ref.entidad_equivalencia e JOIN ing.terceros_publicacion p ON p.id_carga_terceros=e.id_carga_terceros
        WHERE e.origen='JRB' AND e.tipo_parte='ultimoempleador' AND e.id_entidad_representante IS NOT NULL;""")}
        rows=self.cursor.execute("""SELECT DISTINCT f.id_fila,cp.id_caso,cp.id_entidad,cp.id_caso_parte
        FROM ing.archivo a JOIN ing.carga ca ON ca.id_carga=a.id_carga_vigente AND ca.estado='PUBLICADA'
        JOIN ing.fila_origen f ON f.id_carga=ca.id_carga
        JOIN ing.parte_fila pf ON pf.id_fila=f.id_fila AND pf.tipo_parte='ultimoempleador'
        JOIN core.caso_parte cp ON cp.id_caso_parte=pf.id_caso_parte
        WHERE cp.id_entidad IS NOT NULL AND EXISTS(SELECT 1 FROM ing.parte_fila paciente
        WHERE paciente.id_fila=f.id_fila AND paciente.tipo_parte='paciente' AND paciente.id_caso_parte IS NOT NULL);""").fetchall()
        row=next((r for r in rows if canonical.get(r[2],r[2]) in references),None)
        if not row:raise AssertionError('FALTA_CASO_PARA_PRUEBA')
        self.source_row,self.case,self.entity,self.employer_part=row
        self.document=self.cursor.execute('SELECT identificacion_paciente FROM core.caso WHERE id_caso=?;',self.case).fetchone()[0]
        self.load=self.cursor.execute("""INSERT ing.canopus_carga(drive_id,item_id,nombre_archivo,version_origen,hash_contenido,version_parser)
        OUTPUT inserted.id_carga_canopus VALUES(N'PRUEBA',?,N'PRUEBA.XLSX',N'v1',HASHBYTES('SHA2_256',N'PRUEBA'),'prueba-v1');""",str(uuid4())).fetchone()[0]
        self.source=self.cursor.execute("""INSERT ing.canopus_fila(id_carga_canopus,hoja,numero_fila,cedula,
        fecha_audiencia_reportada,reparto_reportado,id_entidad_empleador,estado_empleador)
        OUTPUT inserted.id_fila_canopus VALUES(?,N'hoja1',2,?,'19900101',N'NO_SE_USA_PARA_VINCULAR',?,'ASOCIADO');""",self.load,self.document,self.entity).fetchone()[0]
        self.cursor.execute("UPDATE ing.canopus_carga SET estado='PUBLICADA',vigente=1,publicado_utc=SYSUTCDATETIME(),filas_procesadas=1 WHERE id_carga_canopus=?;",self.load)

    def tearDown(self):
        try:
            self.cursor.execute('IF @@TRANCOUNT>0 ROLLBACK TRANSACTION;')
            after=tuple(self.cursor.execute("SELECT OBJECT_ID(N'ing.canopus_carga',N'U'),"
               "OBJECT_ID(N'ing.canopus_fila',N'U'),OBJECT_ID(N'ref.contacto_reportado',N'U'),"
               "OBJECT_ID(N'rep.vw_contactos_caso',N'V'),"
               "(SELECT id_carga_terceros FROM ing.terceros_publicacion WHERE id=1);").fetchone())
            self.assertEqual(self.before,after)
        finally:self.connection.close();self.closed=True

    def cleanup_connection(self):
        if not self.closed:
            try:self.connection.cursor().execute('IF @@TRANCOUNT>0 ROLLBACK TRANSACTION;')
            finally:self.connection.close();self.closed=True

    def contact(self,origin,role,email,state='UTILIZABLE',field=None):
        address=self.cursor.execute("INSERT mail.direccion(tipo,direccion) OUTPUT inserted.id_direccion VALUES('SMTP',?);",email).fetchone()[0]
        self.cursor.execute("""INSERT ref.contacto_reportado(origen,id_fila_canopus,id_fila_consolidado,
        tipo_parte,campo_origen,id_direccion,estado) VALUES(?,?,?,?,?,?,?);""",origin,
        self.source if origin=='CANOPUS' else None,self.source_row if origin=='CONSOLIDADO' else None,
        role,field or ('correo_electronico' if role=='paciente' else 'email_empresa'),address,state)
        return address

    def selected(self,role):
        return [(r[0],r[1]) for r in self.cursor.execute("SELECT DISTINCT id_direccion,origen FROM rep.vw_contactos_caso WHERE id_caso=? AND tipo_parte=?;",self.case,role)]

    def test_patient_matches_by_cedula_and_consolidado_overrides_canopus(self):
        canopus=self.contact('CANOPUS','paciente','canopus-paciente@example.invalid')
        self.assertEqual(self.selected('paciente'),[(canopus,'CANOPUS')])
        first=self.contact('CONSOLIDADO','paciente','consolidado-uno@example.invalid')
        second=self.contact('CONSOLIDADO','paciente','consolidado-dos@example.invalid')
        self.assertEqual(set(self.selected('paciente')),{(first,'CONSOLIDADO'),(second,'CONSOLIDADO')})

    def test_review_consolidado_contact_does_not_block_canopus_and_inactive_load_is_excluded(self):
        canopus=self.contact('CANOPUS','paciente','canopus-usable@example.invalid')
        self.contact('CONSOLIDADO','paciente','consolidado-revision@example.invalid','REVISION')
        self.assertEqual(self.selected('paciente'),[(canopus,'CANOPUS')])
        self.cursor.execute('UPDATE ing.canopus_carga SET vigente=0 WHERE id_carga_canopus=?;',self.load)
        self.assertEqual(self.selected('paciente'),[])

    def test_employer_prefers_canopus_then_consolidado_and_preserves_entity(self):
        self.assertTrue(self.selected('ultimoempleador'))
        self.assertTrue(all(origin=='MAESTRO_TERCEROS' for _,origin in self.selected('ultimoempleador')))
        canopus=self.contact('CANOPUS','ultimoempleador','canopus-empleador@example.invalid')
        self.assertEqual(self.selected('ultimoempleador'),[(canopus,'CANOPUS')])
        consolidado=self.contact('CONSOLIDADO','ultimoempleador','consolidado-empleador@example.invalid')
        self.assertEqual(self.selected('ultimoempleador'),[(consolidado,'CONSOLIDADO')])
        self.cursor.execute("UPDATE ref.contacto_reportado SET estado='REVISION' WHERE origen='CONSOLIDADO';")
        self.cursor.execute("UPDATE ing.canopus_fila SET id_entidad_empleador=NULL,estado_empleador='SIN_ENTIDAD' WHERE id_fila_canopus=?;",self.source)
        self.assertTrue(self.selected('ultimoempleador'))
        self.assertTrue(all(origin=='MAESTRO_TERCEROS' for _,origin in self.selected('ultimoempleador')))

    def test_invalid_usable_contact_and_unknown_role_are_rejected(self):
        import pyodbc
        with self.assertRaises(pyodbc.IntegrityError):
            self.cursor.execute("""INSERT ref.contacto_reportado(origen,id_fila_canopus,tipo_parte,campo_origen,estado,
            valor_no_utilizable,codigo_revision) VALUES('CANOPUS',?,'paciente','correo_electronico','UTILIZABLE',N'valor invalido','FORMATO');""",self.source)
        address=self.cursor.execute("INSERT mail.direccion(tipo,direccion) OUTPUT inserted.id_direccion VALUES('SMTP',N'fe@example.invalid');").fetchone()[0]
        with self.assertRaises(pyodbc.IntegrityError):
            self.cursor.execute("""INSERT ref.contacto_reportado(origen,id_fila_canopus,tipo_parte,campo_origen,estado,id_direccion)
            VALUES('CANOPUS',?,'por_definir','correo_electronicofe','UTILIZABLE',?);""",self.source,address)


if __name__=='__main__':unittest.main()
