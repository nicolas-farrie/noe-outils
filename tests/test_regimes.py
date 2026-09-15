"""Tests des régimes et exceptions alimentaires — données fictives uniquement."""

import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.dom import minidom

from catering.__main__ import resume
from catering.calcul import tableau
from catering.ods import ecrire_classeur
from catering.regimes import (A_VERIFIER, INCONNU, MENTION_RGPD, REGIMES, STANDARD, Profil,
                              Regimes, champ_regime, normaliser, profils_alimentaires,
                              repartition, signalements)

from tests.test_calcul import JOUR, creneau
from tests.test_encadrants import encadrement


class TestClassement(unittest.TestCase):

    def test_normaliser(self):
        self.assertEqual(normaliser('  Végé   Tarien '), 'vege tarien')

    def test_categories(self):
        attendus = {'Végé': 'Végétarien', 'végétarien': 'Végétarien',
                    'vegan/végétarien': 'Végétalien', 'Vegan': 'Végétalien',
                    'non': STANDARD, '': STANDARD, None: STANDARD,
                    'pas de fromage': A_VERIFIER, 'test': A_VERIFIER}
        self.assertEqual({valeur: REGIMES.categorie(valeur) for valeur in attendus}, attendus)

    def test_exceptions_tous_les_tags_par_defaut(self):
        self.assertEqual(REGIMES.exceptions(['Sans gluten', ' ', "Allergie à l'avoine"]),
                         ('Sans gluten', "Allergie à l'avoine"))

    def test_exceptions_restreintes(self):
        regimes = Regimes(tags_exceptions=('sans gluten',))
        self.assertEqual(regimes.exceptions(['Sans gluten', 'Service d’ordre']), ('Sans gluten',))

    def test_colonnes(self):
        self.assertEqual(REGIMES.colonnes(),
                         (STANDARD, 'Végétarien', 'Végétalien', A_VERIFIER, INCONNU))


class TestLectureDesDonneesNoe(unittest.TestCase):

    def test_champ_repere_par_son_intitule(self):
        champs = {'numeroDeTel_6pq': {'label': 'Numero de tel', 'type': 'phoneNumber'},
                  'regime_x1': {'label': 'As-tu un régime alimentaire particulier ?',
                                'type': 'text'}}
        self.assertEqual(champ_regime(champs), 'regime_x1')
        self.assertIsNone(champ_regime({}))

    def test_profils(self):
        profils = profils_alimentaires([
            {'user': {'_id': 'u1'}, 'formAnswers': {'regime_x1': ' végé '},
             'tags': ['Sans gluten']},
            {'user': {'_id': 'u2'}, 'formAnswers': {}},
            {'user': 'u3'},                              # référence non peuplée : ignorée
        ], 'regime_x1')
        self.assertEqual(profils, {
            'u1': Profil('Végétarien', ('Sans gluten',), 'végé'),
            'u2': Profil(STANDARD, (), ''),
        })


class TestComptages(unittest.TestCase):

    def setUp(self):
        self.jours = tableau([
            creneau('Paula', 10, 12, lieu='A'),
            creneau('Julie', 10, 12, lieu='A'), creneau('Julie', 18, 20, lieu='B'),
            creneau('Max', 10, 12, lieu='A'),
            creneau('Claude', 8, 9, lieu='A'),           # 1 h : pas de repas
            encadrement('Sam', 9, 10, lieu='A'),         # non relié : régime inconnu
        ])
        self.profils = {
            'paula': Profil('Végétarien', (), 'végé'),
            'julie': Profil(STANDARD, ('Sans gluten',), 'non'),
            'max': Profil(A_VERIFIER, (), 'pas de fromage'),
            'claude': Profil(A_VERIFIER, ('Allergie à l\'avoine',), 'test'),
        }

    def test_repartition_par_jour_repas_et_lieu(self):
        resultat = [(r.plage.repas, r.lieu, dict(r.categories), r.avec_exception)
                    for r in repartition(self.jours, self.profils)]
        self.assertEqual(resultat, [
            ('dejeuner', 'A', {STANDARD: 1, 'Végétarien': 1, A_VERIFIER: 1, INCONNU: 1}, 1),
            ('diner', 'B', {STANDARD: 1}, 1),
        ])

    def test_signalements_seulement_pour_les_repas_accordes(self):
        resultat = [(s.plage.repas, s.lieu, s.nom, s.exceptions, s.declaration)
                    for s in signalements(self.jours, self.profils)]
        self.assertEqual(resultat, [
            ('dejeuner', 'A', 'Julie', ('Sans gluten',), ''),
            ('dejeuner', 'A', 'Max', (), 'pas de fromage'),
            ('diner', 'B', 'Julie', ('Sans gluten',), ''),
        ])                                               # Claude n'a aucun repas

    def test_rapport_sans_noms(self):
        rapport = '\n'.join(resume(self.jours, profils=self.profils))
        self.assertIn('Régimes (repas accordés) : Standard 2, Végétarien 1, Végétalien 0, '
                      'À vérifier 1, Inconnu 1', rapport)
        self.assertIn('Exceptions (repas accordés) : Sans gluten 2', rapport)
        for nom in ('Paula', 'Julie', 'Max', 'Claude', 'Sam', 'fromage'):
            self.assertNotIn(nom, rapport)

    def test_rapport_sans_profils(self):
        self.assertNotIn('Régimes', '\n'.join(resume(self.jours)))

    def ecrire(self, **options):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = Path(dossier) / 'repas.ods'
            ecrire_classeur(chemin, self.jours, **options)
            with zipfile.ZipFile(chemin) as archive:
                return archive.read('content.xml').decode('utf-8')

    def test_classeur_avec_regimes(self):
        contenu = self.ecrire(profils=self.profils)
        minidom.parseString(contenu)
        self.assertIn('table:name="Régimes"', contenu)
        # La mention figure deux fois : en tête du récapitulatif et de la feuille Régimes.
        self.assertEqual(contenu.count('protégées par le RGPD (article 9)'), 2)
        self.assertIn('pas de fromage', contenu)

    def test_classeur_sans_regimes_ni_mention(self):
        contenu = self.ecrire()
        self.assertNotIn('table:name="Régimes"', contenu)
        self.assertNotIn(MENTION_RGPD[:40], contenu)


if __name__ == '__main__':
    unittest.main()
