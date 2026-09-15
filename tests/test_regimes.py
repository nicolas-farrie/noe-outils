"""Tests des régimes et exceptions alimentaires — données fictives uniquement."""

import tempfile
import unittest
import zipfile
from collections import Counter
from pathlib import Path
from xml.dom import minidom

from catering.__main__ import resume
from catering.calcul import tableau
from catering.ods import ecrire_classeur
from catering.regimes import (A_VERIFIER, FLEXI, INCONNU, MENTION_RGPD, REGIMES, STANDARD,
                              Profil, Regimes, champ_regime, normaliser, profils_alimentaires,
                              repartition, signalements, tags_utilises)

from tests.test_calcul import JOUR, creneau
from tests.test_encadrants import encadrement


class TestClassement(unittest.TestCase):

    def test_normaliser(self):
        self.assertEqual(normaliser('  Végé   Tarien '), 'vege tarien')

    def test_categories(self):
        attendus = {'Végé': 'Végétarien', 'végétarien': 'Végétarien',
                    'vegan/végétarien': FLEXI, 'Vegan': 'Végétalien',
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

    def test_tags_utilises(self):
        self.assertEqual(tags_utilises([{'tags': ['Sans gluten', ' ']}, {'tags': ['Sans gluten']},
                                        {}]), Counter({'Sans gluten': 2}))

    def test_rapport_liste_tous_les_tags_de_noe(self):
        tags = Counter({'Sans gluten': 2, "Allergie à l'avoine": 1})
        rapport = '\n'.join(resume(self.jours, profils=self.profils, tags_noe=tags))
        self.assertIn("Tags utilisés dans NOÉ (tous pris comme exceptions alimentaires) : "
                      "Allergie à l'avoine 1, Sans gluten 2", rapport)
        restreint = '\n'.join(resume(self.jours, profils=self.profils, tags_noe=tags,
                                     regimes=Regimes(tags_exceptions=('sans gluten',))))
        self.assertIn("non retenus comme exceptions : Allergie à l'avoine", restreint)
        self.assertIn('Tags utilisés dans NOÉ : aucun',
                      '\n'.join(resume(self.jours, tags_noe=Counter())))

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
        # Juste après le récapitulatif, avant les journées.
        self.assertLess(contenu.index('table:name="Régimes"'),
                        contenu.index('table:name="2026-09-26"'))
        self.assertIn('Dont flexi', contenu)
        # La mention figure deux fois : en tête du récapitulatif et de la feuille Régimes.
        self.assertEqual(contenu.count('protégées par le RGPD (article 9)'), 2)
        self.assertIn('pas de fromage', contenu)

    def test_classeur_sans_regimes_ni_mention(self):
        contenu = self.ecrire()
        self.assertNotIn('table:name="Régimes"', contenu)
        self.assertNotIn(MENTION_RGPD[:40], contenu)


class TestFlexi(unittest.TestCase):
    """Flexi-vegan : avec les vegans s'il y en a sur le même repas et le même lieu, sinon
    avec les végétariens."""

    def test_rejoint_les_vegans_ou_les_vegetariens(self):
        jours = tableau([
            creneau('Paula', 10, 12, lieu='A'), creneau('Julie', 10, 12, lieu='A'),
            creneau('Lou', 10, 12, lieu='B'),
        ])
        profils = {'paula': Profil(FLEXI, (), 'vegan/végétarien'),
                   'julie': Profil('Végétalien', (), 'vegan'),
                   'lou': Profil(FLEXI, (), 'vegan/végétarien')}
        resultat = [(r.lieu, dict(r.categories), r.dont_flexi)
                    for r in repartition(jours, profils) if r.plage.repas == 'dejeuner']
        self.assertEqual(resultat, [
            ('A', {'Végétalien': 2}, 1),     # un vegan pur sur place : le plat existe
            ('B', {'Végétarien': 1}, 1),     # pas de vegan : repli sur le végétarien
        ])

    def test_pas_signale_en_cuisine(self):
        jours = tableau([creneau('Paula', 10, 12, lieu='A')])
        self.assertEqual(signalements(jours, {'paula': Profil(FLEXI, (), 'vegan/végétarien')}), [])


if __name__ == '__main__':
    unittest.main()
