"""Tests du classeur produit — structure, formules, valeurs de secours."""

import tempfile
import unittest
import zipfile
from datetime import date, datetime
from pathlib import Path
from xml.dom import minidom

from catering.calcul import Seuils, tableau
from catering.ods import ecrire_classeur

from tests.test_calcul import creneau

AUTRE_JOUR = date(2026, 9, 27)


class TestClasseur(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.jours = tableau([
            creneau('Paula', 12, 13, lieu='Lodève — entrée'),
            creneau('Paula', 13, 18, lieu='Lodève — cuisine'),
            creneau('Claude', 10, 13, lieu='Lodève — entrée'),
            creneau('Julie', 9, 12, lieu='Lodève — entrée', jour=AUTRE_JOUR),
        ])
        cls.dossier = tempfile.TemporaryDirectory()
        cls.chemin = Path(cls.dossier.name) / 'repas.ods'
        ecrire_classeur(cls.chemin, cls.jours, genere_le=datetime(2026, 9, 12, 14, 30))
        with zipfile.ZipFile(cls.chemin) as archive:
            cls.entrees = archive.namelist()
            cls.premiere = archive.infolist()[0]
            cls.contenu = archive.read('content.xml').decode('utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.dossier.cleanup()

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

    def test_seuils_nommes(self):
        for nom in ('nbHeureAM', 'nbHeurePM', 'nbHeureJour'):
            self.assertIn(f'table:named-range table:name="{nom}"', self.contenu)

    def test_colonnes_de_resultat_centrees(self):
        self.assertIn('style:name="centre"', self.contenu)
        self.assertIn('fo:text-align="center"', self.contenu)
        # Heures, déjeuner, dîner : centrés ; noms et lieux, à gauche (sans style).
        self.assertIn('table:style-name="centre" table:formula="of:=[.C4]+[.E4]"', self.contenu)
        self.assertIn('<table:table-cell office:value-type="string"><text:p>Claude</text:p>',
                      self.contenu)

    def test_formules_de_la_premiere_ligne(self):
        self.assertIn('of:=[.C4]+[.E4]', self.contenu)
        self.assertIn('of:=IF(OR([.C4]&gt;=nbHeureAM;[.F4]&gt;=nbHeureJour);1;0)', self.contenu)
        self.assertIn('of:=IF(OR([.E4]&gt;=nbHeurePM;[.F4]&gt;=nbHeureJour);1;0)', self.contenu)

    def test_le_recap_pointe_les_feuilles_de_journee(self):
        self.assertIn("of:=[$'2026-09-26'.B", self.contenu)

    def test_les_valeurs_accompagnent_les_formules(self):
        """Un aperçu qui ne recalcule pas doit afficher les bons chiffres."""
        # Paula, 1 h le matin + 5 h l'après-midi : 6 h dans la journée, deux repas.
        self.assertIn('office:value="6"', self.contenu)
        # Le 26/09 : 2 déjeuners (Paula, Claude) et 1 dîner (Paula).
        self.assertIn('Total de la journée', self.contenu)

    def test_noms_des_benevoles_dans_le_fichier_remis_a_l_equipe(self):
        for nom in ('Paula', 'Claude', 'Julie'):
            self.assertIn(nom, self.contenu)

    def test_seuils_personnalises_recopies_dans_la_feuille(self):
        chemin = Path(self.dossier.name) / 'severe.ods'
        ecrire_classeur(chemin, self.jours, Seuils(am=3.0, pm=5.0, jour=8.0))
        with zipfile.ZipFile(chemin) as archive:
            contenu = archive.read('content.xml').decode('utf-8')
        self.assertIn('office:value="8"', contenu)
        self.assertIn("3 h le matin, 5 h l'après-midi, 8 h dans la journée", contenu)

    def test_classeur_vide_refuse(self):
        with self.assertRaises(ValueError):
            ecrire_classeur(Path(self.dossier.name) / 'vide.ods', {})


if __name__ == '__main__':
    unittest.main()
