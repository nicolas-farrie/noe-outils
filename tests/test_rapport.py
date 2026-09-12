"""Tests du rapport de contrôle — il compte, il ne nomme pas."""

import unittest
from datetime import date

from catering.__main__ import resume
from catering.calcul import Seuils, tableau

from tests.test_calcul import creneau

AUTRE_JOUR = date(2026, 9, 27)


class TestResume(unittest.TestCase):

    def setUp(self):
        self.jours = tableau([
            creneau('Paula', 12, 13, lieu='Lodève — entrée'),
            creneau('Paula', 13, 18, lieu='Lodève — cuisine'),
            creneau('Claude', 10, 13, lieu='Lodève — entrée'),
            creneau('Julie', 9, 12, lieu='Lodève — entrée', jour=AUTRE_JOUR),
        ])
        self.rapport = '\n'.join(resume(self.jours))

    def test_aucun_nom_de_benevole(self):
        for nom in ('Paula', 'Claude', 'Julie'):
            self.assertNotIn(nom, self.rapport)

    def test_comptages_par_jour_et_par_lieu(self):
        self.assertIn('26/09/2026 — 2 bénévole(s) présent(s), 2 déjeuner(s), 1 dîner(s)',
                      self.rapport)
        # Paula : 6 h dans la journée → deux repas, le déjeuner à l'entrée (1 h le matin),
        # le dîner en cuisine (5 h l'après-midi). Claude : 3 h le matin, déjeuner seul.
        self.assertIn('Lodève — entrée                      2 déj.   0 dîn.', self.rapport)
        self.assertIn('Lodève — cuisine                     0 déj.   1 dîn.', self.rapport)

    def test_total_sur_toutes_les_journees(self):
        self.assertIn('Total : 2 journée(s), 3 déjeuner(s), 1 dîner(s)', self.rapport)

    def test_les_seuils_changent_les_comptages(self):
        # nbHeureJour porté à 8 h : Paula perd son déjeuner (1 h le matin seulement).
        severe = '\n'.join(resume(self.jours, Seuils(am=2.0, pm=4.0, jour=8.0)))
        self.assertIn('Total : 2 journée(s), 2 déjeuner(s), 1 dîner(s)', severe)


if __name__ == '__main__':
    unittest.main()
