"""Tests des encadrants — repas de chaque plage encadrée sur un espace, un seul par personne.

Règles (15/09/2026) : seul compte l'encadrant affecté à la session ; il a le repas de chaque
plage que la session touche (matin 08:00–16:00, soir 14:00–03:00), quelle que soit la
durée ; une session sans espace ne donne pas de repas ; relié à une inscription, il est la
même personne que le bénévole et ne mange qu'une fois par plage.
"""

import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from xml.dom import minidom

from catering.__main__ import resume
from catering.calcul import (ENCADRANT, SANS_LIEU, creneaux_encadrement,
                             encadrements_sans_espace, releve_anomalies, tableau,
                             totaux_du_jour, totaux_par_lieu)
from catering.ods import ecrire_classeur
from catering.regles import REGLES_BENEVOLES

from tests.test_calcul import JOUR, creneau

DEJEUNER, DINER = REGLES_BENEVOLES.plages


def encadrement(nom, debut, fin, lieu='A', cle=None):
    """Un créneau d'encadrement ; `cle` = identifiant de la personne inscrite, si reliée."""
    return replace(creneau(nom, debut, fin, lieu), role=ENCADRANT,
                   benevole=cle or f'encadrant:{nom.lower()}')


def ligne_unique(*creneaux):
    lignes = tableau(list(creneaux))[JOUR]
    assert len(lignes) == 1, lignes
    return lignes[0]


class TestPlagesEncadrant(unittest.TestCase):

    def test_le_matin_sans_condition_de_duree(self):
        ligne = ligne_unique(encadrement('Sam', 9, 10))          # 1 h, sous le seuil bénévole
        self.assertEqual(ligne.encadre, {'dejeuner': True, 'diner': False})
        self.assertEqual((ligne.a_droit(DEJEUNER), ligne.a_droit(DINER)), (True, False))

    def test_chevauchement_voulu_des_plages(self):
        ligne = ligne_unique(encadrement('Sam', 14, 15))         # dans 08-16 et dans 14-03
        self.assertEqual((ligne.a_droit(DEJEUNER), ligne.a_droit(DINER)), (True, True))

    def test_le_soir(self):
        ligne = ligne_unique(encadrement('Sam', 17, 18))
        self.assertEqual((ligne.a_droit(DEJEUNER), ligne.a_droit(DINER)), (False, True))

    def test_session_sans_espace_pas_de_repas(self):
        creneaux = [encadrement('Sam', 9, 10, lieu=SANS_LIEU)]
        self.assertEqual(tableau(creneaux), {})
        self.assertEqual(encadrements_sans_espace(creneaux), 1)


class TestUneSeulePersonne(unittest.TestCase):

    def test_encadrant_et_benevole_sur_le_meme_lieu_un_seul_repas(self):
        benevolat = replace(creneau('Sam', 10, 12, lieu='A'), benevole='u1')
        ligne = ligne_unique(benevolat, encadrement('Sam', 10, 13, lieu='A', cle='u1'))
        self.assertEqual(totaux_du_jour([ligne]), {'dejeuner': 1, 'diner': 0})

    def test_deux_lieux_dans_la_plage_repas_au_lieu_dominant(self):
        benevolat = replace(creneau('Sam', 8, 12, lieu='B'), benevole='u1')   # 4 h sur B
        ligne = ligne_unique(benevolat, encadrement('Sam', 13, 14, lieu='A', cle='u1'))
        self.assertEqual(totaux_par_lieu([ligne]), {'B': {'dejeuner': 1, 'diner': 0}})

    def test_encadrant_non_relie_reste_une_personne_a_part(self):
        # Aucun rapprochement par le nom : c'est à l'équipe de relier les fiches dans NOÉ.
        lignes = tableau([creneau('Sam', 10, 12, lieu='A'), encadrement('Sam', 10, 12)])[JOUR]
        self.assertEqual(len(lignes), 2)


class TestLectureDesDonneesNoe(unittest.TestCase):

    SESSIONS = [
        {'_id': 's1', 'start': '2026-09-26T08:00:00.000Z', 'end': '2026-09-26T10:00:00.000Z',
         'places': [{'_id': 'p1'}],
         'stewards': [{'_id': 'st1', 'firstName': 'Sam', 'lastName': 'Dupont'}]},
        {'_id': 's2', 'start': '2026-09-26T08:00:00.000Z', 'end': '2026-09-26T10:00:00.000Z',
         'places': [{'_id': 'p1'}], 'stewards': []},
        {'_id': 's3', 'start': '2026-09-26T15:00:00.000Z', 'end': '2026-09-26T16:00:00.000Z',
         'stewards': ['st2']},
        {'_id': 's4', 'start': None, 'end': None, 'stewards': ['st2']},
    ]
    FICHES = [{'_id': 'st1', 'firstName': 'Sam', 'lastName': 'Dupont'},
              {'_id': 'st2', 'firstName': 'Lou', 'lastName': 'Martin'}]
    INSCRIPTIONS = [{'user': {'_id': 'u1', 'firstName': 'Samuel', 'lastName': 'Dupont'},
                     'steward': {'_id': 'st1'}, 'sessionsSubscriptions': []}]
    LIEUX = {'p1': 'Lodève — entrée'}

    def test_affectations_de_session_et_lien_avec_l_inscription(self):
        creneaux = creneaux_encadrement(self.SESSIONS, self.INSCRIPTIONS, self.FICHES, self.LIEUX)
        self.assertEqual(
            [(c.benevole, c.nom, c.lieu, c.role, c.debut.hour) for c in creneaux],
            [('u1', 'Samuel Dupont', 'Lodève — entrée', ENCADRANT, 10),   # relié : l'inscrit
             ('encadrant:st2', 'Lou Martin', SANS_LIEU, ENCADRANT, 17)])  # nom lu dans la fiche


class TestSorties(unittest.TestCase):

    def setUp(self):
        self.creneaux = [encadrement('Sam', 9, 10), creneau('Julie', 10, 12, lieu='A')]

    def test_rapport(self):
        rapport = '\n'.join(resume(tableau(self.creneaux), sans_espace=2))
        self.assertIn('26/09/2026 — 2 présent(s) dont 1 encadrant(s), 2 déjeuner(s), 0 dîner(s)',
                      rapport)
        self.assertIn('Encadrements sans espace, sans repas : 2', rapport)
        for nom in ('Sam', 'Julie'):
            self.assertNotIn(nom, rapport)

    def test_anomalie_d_encadrement(self):
        anomalie, = releve_anomalies([encadrement('Sam', 5, 9)])
        self.assertEqual((anomalie.creneau.role, anomalie.heures), (ENCADRANT, 3))

    def test_classeur(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = Path(dossier) / 'repas.ods'
            ecrire_classeur(chemin, tableau(self.creneaux))
            with zipfile.ZipFile(chemin) as archive:
                contenu = archive.read('content.xml').decode('utf-8')
        minidom.parseString(contenu)
        self.assertIn('Encadre matin', contenu)
        self.assertIn('Encadrants sur un espace', contenu)


if __name__ == '__main__':
    unittest.main()
