"""Tests du classeur produit — structure, formules, valeurs de secours."""

import tempfile
import unittest
import zipfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from xml.dom import minidom

from catering.calcul import releve_anomalies, tableau
from catering.ods import ecrire_classeur
from catering.regles import REGLES_BENEVOLES

from tests.test_calcul import LENDEMAIN, creneau


def contenu(chemin):
    with zipfile.ZipFile(chemin) as archive:
        return archive.read('content.xml').decode('utf-8')


class TestClasseur(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.creneaux = [
            creneau('Paula', 10, 12, lieu='Lodève — entrée'),
            creneau('Paula', 18, 20, lieu='Lodève — cuisine'),
            creneau('Claude', 14, 16, lieu='Lodève — entrée'),
            creneau('Julie', 9, 12, lieu='Lodève — entrée', jour=LENDEMAIN),
        ]
        cls.jours = tableau(cls.creneaux)
        cls.dossier = tempfile.TemporaryDirectory()
        cls.chemin = Path(cls.dossier.name) / 'repas.ods'
        ecrire_classeur(cls.chemin, cls.jours, genere_le=datetime(2026, 9, 15, 14, 30))
        with zipfile.ZipFile(cls.chemin) as archive:
            cls.entrees = archive.namelist()
            cls.premiere = archive.infolist()[0]
        cls.contenu = contenu(cls.chemin)

    @classmethod
    def tearDownClass(cls):
        cls.dossier.cleanup()

    def ecrire(self, nom, *args, **kwargs):
        chemin = Path(self.dossier.name) / nom
        ecrire_classeur(chemin, *args, **kwargs)
        return contenu(chemin)

    def test_archive_conforme(self):
        # Le mimetype doit être la première entrée, non compressée.
        self.assertEqual(self.premiere.filename, 'mimetype')
        self.assertEqual(self.premiere.compress_type, zipfile.ZIP_STORED)
        for attendu in ('META-INF/manifest.xml', 'content.xml', 'styles.xml', 'meta.xml'):
            self.assertIn(attendu, self.entrees)

    def test_xml_bien_forme(self):
        minidom.parseString(self.contenu)   # lève si le XML est cassé

    def test_une_feuille_par_journee_plus_recap_et_variables(self):
        for nom in ('"Récap"', '"2026-09-26"', '"2026-09-27"', '"Variables"'):
            self.assertIn(f'table:name={nom}', self.contenu)
        self.assertNotIn('table:name="Anomalies"', self.contenu)

    def test_seuils_nommes(self):
        for nom in ('seuilDejeuner', 'seuilDiner'):
            self.assertIn(f'table:named-range table:name="{nom}"', self.contenu)

    def test_formules_de_la_premiere_ligne(self):
        # Colonnes : A nom ; B lieu, C heures, D encadre (matin) ; E lieu, F heures,
        # G encadre (soir) ; H déjeuner, I dîner.
        self.assertIn('of:=IF(OR(AND([.C4]&gt;0;[.C4]&gt;=seuilDejeuner);[.D4]=1);1;0)',
                      self.contenu)
        self.assertIn('of:=IF(OR(AND([.F4]&gt;0;[.F4]&gt;=seuilDiner);[.G4]=1);1;0)',
                      self.contenu)
        self.assertIn('of:=SUMIFS([.H$4:.H$5];[.B$4:.B$5];', self.contenu)
        self.assertIn('of:=SUMIFS([.I$4:.I$5];[.E$4:.E$5];', self.contenu)

    def test_colonnes_de_resultat_centrees(self):
        self.assertIn('table:style-name="centre" table:formula="of:=IF(OR(AND([.C4]&gt;0',
                      self.contenu)
        self.assertIn('<table:table-cell office:value-type="string"><text:p>Claude</text:p>',
                      self.contenu)

    def test_le_recap_pointe_les_feuilles_de_journee(self):
        self.assertIn("of:=[$'2026-09-26'.B", self.contenu)

    def test_regles_rappelees(self):
        self.assertIn('déjeuner : ≥ 2 h entre 08:00 et 16:00', self.contenu)
        self.assertIn('Fenêtre du déjeuner (12:00–14:00) : désactivée.', self.contenu)
        self.assertIn('dîner : présence entre 14:00 et 03:00', self.contenu)

    def test_noms_des_benevoles_dans_le_fichier_remis_a_l_equipe(self):
        for nom in ('Paula', 'Claude', 'Julie'):
            self.assertIn(nom, self.contenu)

    def test_seuils_personnalises_recopies_dans_la_feuille(self):
        texte = self.ecrire('severe.ods', self.jours, REGLES_BENEVOLES.avec_seuils(diner=5))
        self.assertIn('dîner : ≥ 5 h entre 16:00 et 03:00', texte)
        self.assertIn('office:value="5"', texte)

    def test_feuille_anomalies_quand_il_y_en_a(self):
        creneaux = self.creneaux + [creneau('Claude', 5, 9, lieu='Lodève — entrée')]
        texte = self.ecrire('anomalies.ods', tableau(creneaux),
                            anomalies=releve_anomalies(creneaux))
        minidom.parseString(texte)
        self.assertIn('table:name="Anomalies"', texte)
        self.assertIn('26/09 05:00', texte)

    def test_fenetre_active_ecrit_une_valeur_sans_formule(self):
        dejeuner, diner = REGLES_BENEVOLES.plages
        regles = replace(REGLES_BENEVOLES,
                         plages=(replace(dejeuner, presence_fenetre=0.5), diner))
        texte = self.ecrire('fenetre.ods', tableau(self.creneaux, regles), regles)
        self.assertNotIn('seuilDejeuner)', texte)
        self.assertIn('seuilDiner)', texte)

    def test_classeur_vide_refuse(self):
        with self.assertRaises(ValueError):
            ecrire_classeur(Path(self.dossier.name) / 'vide.ods', {})


if __name__ == '__main__':
    unittest.main()
