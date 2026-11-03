"""Integracion opt-in contra el catalogo JRB. Las escrituras de fallo se revierten."""
import copy
import gzip
import json
import os
import unittest
from unittest.mock import patch

from terceros import repository
from terceros.domain import digest


@unittest.skipUnless(os.environ.get("TERCEROS_SQL_INSTANTANEA"), "Requiere instantanea e identidad SQL explicitas")
class SqlCatalogTests(unittest.TestCase):
    def state(self):
        connection=repository.connect("JRB")
        try:
            return tuple(connection.cursor().execute("""SELECT
             (SELECT COUNT_BIG(*) FROM ing.terceros_carga),
             (SELECT COUNT_BIG(*) FROM ref.entidad_correo),
             (SELECT COUNT_BIG(*) FROM ref.entidad_correo_fuente),
             (SELECT COUNT_BIG(*) FROM ing.terceros_pendiente),
             (SELECT COUNT_BIG(*) FROM ref.entidad_correo_revision),
             (SELECT COUNT_BIG(*) FROM mail.direccion),
             (SELECT COUNT_BIG(*) FROM ref.entidad_equivalencia),
             (SELECT id_carga_terceros FROM ing.terceros_publicacion WHERE id=1);""").fetchone())
        finally:
            connection.close()

    def test_replay_rollback_and_sql_patient_constraint(self):
        import pyodbc
        with gzip.open(os.environ["TERCEROS_SQL_INSTANTANEA"],"rt",encoding="utf-8") as stream:
            document=json.load(stream)
        before=self.state()
        result=repository.apply(document)
        self.assertTrue(result["reutilizada"])
        self.assertEqual(self.state(),before)

        altered=copy.deepcopy(document)
        altered["datos"]["conteos"]["PRUEBA_ROLLBACK_NO_PUBLICAR"]=1
        altered["sha256"]=digest(altered["datos"])
        real_connect=repository.connect

        class CursorProxy:
            def __init__(self,inner):object.__setattr__(self,"inner",inner)
            def __getattr__(self,name):return getattr(self.inner,name)
            def __setattr__(self,name,value):setattr(self.inner,name,value)
            def execute(self,sql,*values):
                if sql.startswith(("UPDATE ing.terceros_publicacion","INSERT ing.terceros_publicacion")):
                    raise RuntimeError("FALLO_CONTROLADO")
                return self.inner.execute(sql,*values)
            def executemany(self,sql,values):
                return self.inner.executemany(sql,values)

        class ConnectionProxy:
            def __init__(self,inner):self.inner=inner
            def cursor(self):return CursorProxy(self.inner.cursor())
            def close(self):self.inner.close()

        with patch.object(repository,"connect",side_effect=lambda *a,**kw:ConnectionProxy(real_connect(*a,**kw))):
            with self.assertRaisesRegex(RuntimeError,"FALLO_CONTROLADO"):
                repository.apply(altered)
        self.assertEqual(self.state(),before)

        connection=repository.connect("JRB",write=True)
        cursor=connection.cursor()
        try:
            cursor.execute(repository.SESSION_OPTIONS)
            cursor.execute("BEGIN TRANSACTION;")
            example=cursor.execute("SELECT TOP(1) id_entidad,id_direccion FROM ref.entidad_correo;").fetchone()
            with self.assertRaises(pyodbc.IntegrityError):
                cursor.execute("INSERT ref.entidad_correo(id_entidad,tipo_parte,id_direccion) VALUES(?,'paciente',?);",example[0],example[1])
        finally:
            cursor.execute("IF @@TRANCOUNT>0 ROLLBACK TRANSACTION;")
            connection.close()
        self.assertEqual(self.state(),before)


if __name__=="__main__":unittest.main()
