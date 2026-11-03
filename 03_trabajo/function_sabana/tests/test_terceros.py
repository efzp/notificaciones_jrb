from __future__ import annotations

import copy
import unittest
from datetime import date

from terceros.domain import Catalog, digest, emails, subject_role, validate_snapshot

ENTITIES = [{"id_entidad":1,"nombre":"EMPRESA SINTETICA S.A."},
            {"id_entidad":2,"nombre":"OTRA EMPRESA"}]


def jnc(**changes):
    value = dict(rol="EMPLEADOR",entidad="EMPRESA SINTETICA S.A.",correo="contacto@example.invalid",
                 fuente="CALIFICACION",id_origen=1,cedula="123456789",radicado="R1",audiencia="2026-06-01",
                 fecha="2026-06-02",certificado_exacto=False)
    value.update(changes)
    return value


def catalog():
    return Catalog(ENTITIES,date(2026,10,29))


class NormalizationTests(unittest.TestCase):
    def test_lists_preserve_local_case_and_do_not_repair_typos(self):
        found,issues=emails("Contacto@EXAMPLE.INVALID; otro@gmaill.com")
        self.assertEqual(found,["Contacto@example.invalid","otro@gmaill.com"])
        self.assertEqual(issues,[])

    def test_invalid_addresses_are_not_discarded_as_valid(self):
        for value in ("sin-correo","a@@example.invalid","a..b@example.invalid","a@-example.invalid"):
            with self.subTest(value=value):
                found,issues=emails(value)
                self.assertEqual(found,[])
                self.assertTrue(issues)

    def test_patient_replies_and_ambiguous_roles_are_excluded(self):
        for s in ("NOTIFICACION PACIENTE: PERSONA EPS", "RV: NOTIFICACION PACIENTE: PERSONA EPS",
                  "RE: NOTIFICACION EMPLEADOR", "NOTIFICACION EPS Y AFP"):
            self.assertIsNone(subject_role(s))
        self.assertEqual(subject_role("RV: NOTIFICACION EMPLEADOR NOMBRE: PERSONA"),"ultimoempleador")


class CatalogTests(unittest.TestCase):
    def test_patients_never_create_associations_or_pending_patient_data(self):
        c=catalog();c.jnc([jnc(rol="PACIENTE"),jnc(rol="PACIENTES")])
        self.assertEqual(c.payload()["asociaciones"],[])
        self.assertEqual(c.payload()["pendientes"],[])

    def test_duplicate_rows_sources_and_resends_count_one_case(self):
        c=catalog()
        c.jnc([jnc(),jnc(),jnc(fuente="ESPERADA",id_origen=2,certificado_exacto=True)])
        group=c.payload()["asociaciones"][0]
        self.assertEqual(group["casos_distintos"],1)
        self.assertEqual(group["observaciones"],2)
        self.assertEqual(group["casos_certificado_exacto"],1)

    def test_future_dates_are_visible_without_becoming_historical_validity(self):
        c=catalog();c.jnc([jnc(fecha="2062-01-01")])
        p=c.payload()
        self.assertIsNone(p["asociaciones"][0]["primera_observacion"])
        self.assertEqual(p["pendientes"][0]["codigo"],"FECHA_FUERA_RANGO")
        self.assertNotIn("autorizado",p["asociaciones"][0])

    def test_unmatched_and_ambiguous_entity_names_are_pending(self):
        c=Catalog(ENTITIES+[{"id_entidad":3,"nombre":"EMPRESA SINTETICA SA"}],date(2026,10,29))
        c.jnc([jnc(entidad="Desconocida"),jnc(entidad="Empresa sintetica s.a.")])
        self.assertEqual(len(c.payload()["asociaciones"]),1)  # SA y S A ahora se agrupan.
        c=Catalog(ENTITIES+[{"id_entidad":3,"nombre":"Empresa sintetica SAS"}],date(2026,10,29))
        c.jnc([jnc(entidad="Empresa sintetica")])
        self.assertEqual(c.payload()["asociaciones"],[])
        self.assertEqual(c.payload()["pendientes"][0]["codigo"],"ENTIDAD_AMBIGUA")

    def test_independent_source_windows_and_order_independent_fingerprint(self):
        observations=[jnc(),jnc(id_origen=2,radicado="R2",fecha="2026-07-01")]
        first=catalog();first.jnc(observations)
        second=catalog();second.jnc(reversed(observations))
        self.assertEqual(digest(first.payload()),digest(second.payload()))
        document={"datos":first.payload(),"sha256":digest(first.payload())}
        validate_snapshot(document)
        corrupt=copy.deepcopy(document);corrupt["datos"]["asociaciones"][0]["correo"]="otro@example.invalid"
        with self.assertRaisesRegex(ValueError,"INSTANTANEA_INVALIDA"):
            validate_snapshot(corrupt)

    def test_denominator_is_distinct_cases_not_sum_of_recipient_frequencies(self):
        c=catalog();c.jnc([jnc(correo="a@example.invalid;b@example.invalid"),
                           jnc(id_origen=2,radicado="R2",correo="a@example.invalid")])
        a,b=c.payload()["asociaciones"]
        self.assertEqual((a["casos_distintos"],b["casos_distintos"]),(2,1))
        self.assertEqual(a["casos_entidad_con_correo"],2)
        self.assertEqual(b["casos_entidad_con_correo"],2)

    def test_ambiguous_case_or_two_insurers_does_not_create_cartesian_links(self):
        c=catalog()
        cases=[dict(id_caso=1,documento="123456789",rol="arl",id_entidad=1),
               dict(id_caso=1,documento="123456789",rol="arl",id_entidad=2)]
        message=dict(id_mensaje=1,asunto="NOTIFICACION ARL C.C:123456789",clase="IPM.Note",
                     remitente="Notificaciones Sala3",flags=17,para=["a@example.invalid"],fecha="2026-06-02")
        c.pst(cases,[message])
        self.assertFalse(c.payload()["asociaciones"])
        self.assertEqual(c.payload()["pendientes"][0]["codigo"],"CASO_ENTIDAD_AMBIGUO")

    def test_pst_reports_replies_and_cc_are_not_extra_sends(self):
        c=catalog();cases=[dict(id_caso=1,documento="123456789",rol="ultimoempleador",id_entidad=1)]
        message=dict(id_mensaje=1,asunto="NOTIFICACION EMPLEADOR C.C:123456789",clase="IPM.Note",
                     remitente="Notificaciones Sala3",flags=17,para=["a@example.invalid"],fecha="2026-06-02")
        c.pst(cases,[message,dict(message,id_mensaje=2,asunto="RV: "+message["asunto"]),
                     dict(message,id_mensaje=3,clase="REPORT.IPM.Note.DR"),
                     dict(message,id_mensaje=4,asunto="RE: "+message["asunto"])])
        group=c.payload()["asociaciones"][0]
        self.assertEqual(group["casos_distintos"],1)
        self.assertEqual(group["observaciones"],2)

    def test_missing_jnc_business_identity_is_pending_not_a_fake_distinct_case(self):
        c=catalog();c.jnc([jnc(cedula=None)])
        self.assertEqual(c.payload()["asociaciones"],[])
        self.assertEqual(c.payload()["pendientes"][0]["codigo"],"CASO_JNC_SIN_IDENTIDAD")

    def test_a_rehashed_snapshot_cannot_insert_patient_pending_rows(self):
        c=catalog();c.jnc([jnc(entidad="SIN EQUIVALENCIA")])
        payload=c.payload();payload["pendientes"][0]["tipo_parte"]="paciente"
        with self.assertRaisesRegex(ValueError,"PENDIENTE_INVALIDO"):
            validate_snapshot({"datos":payload,"sha256":digest(payload)})


if __name__ == "__main__":
    unittest.main()
