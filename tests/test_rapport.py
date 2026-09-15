"""Tests du rapport de contrôle — il compte, il ne nomme pas."""

import unittest

from catering.__main__ import resume
from catering.calcul import Anomalie, releve_anomalies, tableau
from catering.regles import REGLES_BENEVOLES

from tests.test_calcul import LENDEMAIN, creneau


class TestResume(unittest.TestCase):

    def setUp(self):
        self.creneaux = [
            creneau('Paula', 10, 12, lieu='Lodève — entrée'),
            creneau('Paula', 18, 20, lieu='Lodève — cuisine'),
            creneau('Claude', 14, 16, lieu='Lodève — entrée'),
            creneau('Julie', 9, 12, lieu='Lodève — entrée', jour=LENDEMAIN),
        ]
        self.jours = tableau(self.creneaux)
        self.rapport = '\n'.join(resume(self.jours))

    def test_aucun_nom_de_benevole(self):
        anomalies = [Anomalie(creneau('Paula', 5, 9), 3.0)]
        rapport = '\n'.join(resume(self.jours, anomalies=anomalies))
        for nom in ('Paula', 'Claude', 'Julie'):
            self.assertNotIn(nom, rapport)

    def test_comptages_par_jour_et_par_lieu(self):
        self.assertIn('26/09/2026 — 2 bénévole(s) présent(s), 2 déjeuner(s), 1 dîner(s)',
                      self.rapport)
        # Paula : déjeuner à l'entrée (matin), dîner en cuisine (soir). Claude : déjeuner
        # à l'entrée, 14 h-16 h tombant dans la plage du matin.
        self.assertIn('Lodève — entrée                      2 déj.   0 dîn.', self.rapport)
        self.assertIn('Lodève — cuisine                     0 déj.   1 dîn.', self.rapport)

    def test_total_sur_toutes_les_journees(self):
        self.assertIn('Total : 2 journée(s), 3 déjeuner(s), 1 dîner(s)', self.rapport)
        self.assertNotIn('Anomalies', self.rapport)

    def test_anomalies_comptees(self):
        creneaux = self.creneaux + [creneau('Claude', 5, 9, lieu='Lodève — entrée')]
        rapport = '\n'.join(resume(tableau(creneaux), anomalies=releve_anomalies(creneaux)))
        self.assertIn('Anomalies : 1 créneau(x), 3 h hors des plages horaires', rapport)

    def test_les_seuils_changent_les_comptages(self):
        # Seuil du déjeuner porté à 3 h : Paula (2 h) et Claude (2 h) le perdent, pas Julie.
        severes = REGLES_BENEVOLES.avec_seuils(dejeuner=3)
        rapport = '\n'.join(resume(tableau(self.creneaux, severes), severes))
        self.assertIn('Total : 2 journée(s), 1 déjeuner(s), 1 dîner(s)', rapport)


if __name__ == '__main__':
    unittest.main()
