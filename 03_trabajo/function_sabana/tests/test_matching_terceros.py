import unittest
from terceros.matching import EntityMatcher, normalize, score


def matcher(*names,threshold=95):
    return EntityMatcher([{"id_entidad":i+1,"nombre":n} for i,n in enumerate(names)],threshold=threshold)


class EntityMatchingTests(unittest.TestCase):
    def test_empty_or_placeholder_entity_is_not_a_canonical_match(self):
        m=matcher(".","S.A.","EMPRESA REAL SAS")
        self.assertIsNone(m.representative(1,"ultimoempleador"))
        self.assertIsNone(m.representative(2,"ultimoempleador"))
        self.assertIsNone(m.resolve(".","ultimoempleador")["id_entidad"])
        for row in m.export()["equivalencias"]:
            if row["id_entidad_origen"] in {1,2}:
                self.assertEqual(row["metodo"],"SIN_EQUIVALENCIA")
    def test_regex_roles_punctuation_and_legal_abbreviations(self):
        m=matcher("PORVENIR S.A - AFP","SALUD TOTAL - EPS","ARL SURA")
        for name,role,eid in (("porvenir_s_a","afp",1),("SALUD TOTAL EPS","eps",2),("SURA ARL","arl",3)):
            result=m.resolve(name,role)
            self.assertEqual(result["id_entidad"],eid)
            self.assertEqual(result["puntaje"],100)

    def test_legal_forms_and_context_cannot_be_bridged(self):
        m=matcher("EMPRESA ALFA SA","EMPRESA ALFA","EMPRESA ALFA SAS")
        self.assertNotEqual(m.representative(1,"ultimoempleador"),m.representative(3,"ultimoempleador"))
        self.assertIsNone(m.resolve("EMPRESA ALFA","ultimoempleador")["id_entidad"])
        m=matcher("EPS SURA","ARL SURA")
        self.assertNotEqual(m.representative(1,"remitente"),m.representative(2,"remitente"))
        self.assertEqual(m.resolve("SURA ARL","remitente")["id_entidad"],2)

    def test_life_and_general_insurance_stay_separate(self):
        m=matcher("SEGUROS DE VIDA ALFA SA","SEGUROS GENERALES ALFA SA")
        self.assertNotEqual(m.representative(1,"aseguradora"),m.representative(2,"aseguradora"))

    def test_minimum_margin_rejects_two_plausible_candidates(self):
        m=matcher("TECNOLOGIA AVANZADA CENTRAL 1","TECNOLOGIA AVANZADA CENTRAL 2")
        result=m.resolve("TECNOLOGIA AVANZADA CENTRAL","ultimoempleador")
        self.assertIsNone(result["id_entidad"])
        self.assertEqual(result["metodo"],"AMBIGUA")
        self.assertEqual(result["puntaje"],result["segundo_puntaje"])

    def test_close_typo_uses_proximity_but_short_brands_do_not(self):
        m=matcher("FIDUCIARIA COLPATRIA","SURA")
        result=m.resolve("FIDUSIARIA COLPATRIA","ultimoempleador")
        self.assertEqual(result["id_entidad"],1)
        self.assertEqual(result["metodo"],"PROXIMIDAD")
        self.assertIsNone(m.resolve("SURE","ultimoempleador")["id_entidad"])

    def test_regex_does_not_give_subset_names_a_perfect_score(self):
        a=normalize("CONSTRUCTORA BOLIVAR BOGOTA SA","ultimoempleador")
        b=normalize("BOLIVAR SA","ultimoempleador")
        self.assertLess(score(a,b),95)

    def test_complete_link_prevents_transitive_chain(self):
        m=matcher("SERVICIOS EMPRESARIALES INTERNACIONALES ABCD",
                  "SERVICIOS EMPRESARIALES INTERNACIONALES ABCE",
                  "SERVICIOS EMPRESARIALES INTERNACIONALES ABFE",threshold=97)
        self.assertEqual(m.representative(1,"ultimoempleador"),m.representative(2,"ultimoempleador"))
        self.assertNotEqual(m.representative(1,"ultimoempleador"),m.representative(3,"ultimoempleador"))

    def test_role_blocking_and_deterministic_name_selection(self):
        entities=[{"id_entidad":1,"nombre":"ARL SURA"},{"id_entidad":2,"nombre":"EPS SURA"}]
        roles=[{"id_entidad":1,"tipo_parte":"arl"},{"id_entidad":2,"tipo_parte":"eps"}]
        a=EntityMatcher(entities,roles);b=EntityMatcher(reversed(entities),roles)
        for m,order in ((a,["sura_arl","SURA ARL"]),(b,["SURA ARL","sura_arl"])):
            for n in order:m.resolve(n,"arl")
        self.assertEqual(a.export(),b.export())
        self.assertIsNone(a.resolve("ARL SURA","eps")["id_entidad"])


if __name__=="__main__":unittest.main()
