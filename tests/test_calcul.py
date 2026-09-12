"""Tests du calcul des repas — données fictives, aucun appel réseau.

Lancement depuis la racine du dépôt :

    python3 -m unittest discover -s tests -t .
"""

import unittest
from datetime import date, datetime, time

from catering.calcul import (
    FUSEAU, SANS_LIEU, SEUILS_DEFAUT, Creneau, creneaux_depuis_noe, lignes_du_jour,
    heures_par_jour, tableau, totaux_du_jour, totaux_par_lieu,
)

JOUR = date(2026, 9, 26)


def creneau(nom, debut, fin, lieu=SANS_LIEU, jour=JOUR):
    """Un créneau en heures locales pleines, pour écrire les cas lisiblement."""
    return Creneau(
        benevole=nom.lower(),
        nom=nom,
        debut=datetime.combine(jour, time(debut), tzinfo=FUSEAU),
        fin=datetime.combine(jour, time(fin), tzinfo=FUSEAU),
        lieu=lieu,
    )


class TestExempleDuReadme(unittest.TestCase):
    """Le tableau du README : Paula, Jacques, Julie, Nicolas, Claude → 2 déjeuners, 4 dîners."""

    def setUp(self):
        self.lignes = lignes_du_jour(heures_par_jour([
            creneau('Paula', 12, 13), creneau('Paula', 13, 17),      # AM 1, PM 4
            creneau('Jacques', 10, 13), creneau('Jacques', 13, 16),  # AM 3, PM 3
            creneau('Julie', 13, 17),                                # AM 0, PM 4
            creneau('Nicolas', 13, 18),                              # AM 0, PM 5
            creneau('Claude', 10, 13),                               # AM 3, PM 0
        ])[JOUR])
        self.repas = {ligne.nom: (ligne.dejeuner(), ligne.diner()) for ligne in self.lignes}

    def test_heures(self):
        heures = {ligne.nom: (ligne.am, ligne.pm) for ligne in self.lignes}
        self.assertEqual(heures, {
            'Paula': (1, 4), 'Jacques': (3, 3), 'Julie': (0, 4),
            'Nicolas': (0, 5), 'Claude': (3, 0),
        })

    def test_repas_ligne_a_ligne(self):
        self.assertEqual(self.repas, {
            'Paula': (False, True),     # 1 h le matin : pas de déjeuner ; 4 h l'après-midi : dîner
            'Jacques': (True, True),    # 3 h le matin ; 6 h dans la journée
            'Julie': (False, True),
            'Nicolas': (False, True),
            'Claude': (True, False),
        })

    def test_totaux(self):
        self.assertEqual(totaux_du_jour(self.lignes), (2, 4))


class TestTotalJournee(unittest.TestCase):
    """Le total de la journée l'emporte sur les seuils de demi-journée (cas de Nicolas)."""

    def test_une_heure_le_matin_et_cinq_l_apres_midi_donnent_deux_repas(self):
        lignes = lignes_du_jour(heures_par_jour([
            creneau('Nicolas', 12, 13, lieu='Lieu A'),
            creneau('Nicolas', 13, 18, lieu='Lieu B'),
        ])[JOUR])
        ligne, = lignes
        self.assertEqual((ligne.am, ligne.pm, ligne.jour), (1, 5, 6))
        self.assertTrue(ligne.dejeuner())   # 1 h < 2 h, mais 6 h dans la journée
        self.assertTrue(ligne.diner())

    def test_chaque_repas_va_au_lieu_ou_la_personne_fait_ses_heures(self):
        lignes = lignes_du_jour(heures_par_jour([
            creneau('Nicolas', 12, 13, lieu='Lieu A'),
            creneau('Nicolas', 13, 18, lieu='Lieu B'),
        ])[JOUR])
        self.assertEqual(totaux_par_lieu(lignes), {'Lieu A': (1, 0), 'Lieu B': (0, 1)})

    def test_six_heures_le_matin_ouvrent_le_diner_sur_le_lieu_du_matin(self):
        # Demi-journée du soir vide : le dîner ne doit pas se perdre.
        lignes = lignes_du_jour(heures_par_jour([
            creneau('Paula', 8, 13, lieu='Lieu A'),
            creneau('Paula', 13, 14, lieu='Lieu A'),
        ])[JOUR])
        self.assertEqual(totaux_par_lieu(lignes), {'Lieu A': (1, 1)})


class TestDecoupage(unittest.TestCase):

    def test_creneau_a_cheval_sur_treize_heures(self):
        ligne, = lignes_du_jour(heures_par_jour([creneau('Julie', 11, 15)])[JOUR])
        self.assertEqual((ligne.am, ligne.pm), (2, 2))

    def test_la_nuit_ne_compte_pas(self):
        nuit = Creneau('julie', 'Julie',
                       datetime.combine(JOUR, time(22), tzinfo=FUSEAU),
                       datetime.combine(date(2026, 9, 27), time(2), tzinfo=FUSEAU))
        presences = heures_par_jour([nuit])
        self.assertEqual(list(presences), [JOUR])
        ligne, = lignes_du_jour(presences[JOUR])
        self.assertEqual((ligne.am, ligne.pm), (0, 2))

    def test_creneaux_qui_se_chevauchent_comptes_une_fois(self):
        lignes = lignes_du_jour(heures_par_jour([
            creneau('Claude', 8, 12, lieu='Lieu A'),
            creneau('Claude', 10, 13, lieu='Lieu B'),
        ])[JOUR])
        ligne, = lignes
        self.assertEqual(ligne.am, 5)            # 08-13, pas 4 + 3
        self.assertEqual(ligne.lieu_matin, 'Lieu A')   # 4 h contre 1 h

    def test_lieux_a_egalite_le_premier_commence_l_emporte(self):
        lignes = lignes_du_jour(heures_par_jour([
            creneau('Julie', 8, 10, lieu='Lieu A'),
            creneau('Julie', 11, 13, lieu='Lieu B'),
        ])[JOUR])
        self.assertEqual(lignes[0].lieu_matin, 'Lieu A')


class TestSeuilsPersonnalises(unittest.TestCase):

    def test_les_seuils_sont_des_variables(self):
        from catering.calcul import Seuils
        ligne, = lignes_du_jour(heures_par_jour([creneau('Claude', 11, 13)])[JOUR])
        self.assertTrue(ligne.dejeuner(SEUILS_DEFAUT))
        self.assertFalse(ligne.dejeuner(Seuils(am=3.0, pm=4.0, jour=6.0)))


class TestLectureDesDonneesNoe(unittest.TestCase):
    """Passage des structures de l'API aux créneaux : UTC → heure locale, lieux, filtres."""

    SESSIONS = [
        {'_id': 's1', 'start': '2026-09-26T06:00:00.000Z', 'end': '2026-09-26T11:00:00.000Z',
         'places': [{'_id': 'p1'}]},                       # 08:00-13:00 à Paris
        {'_id': 's2', 'start': '2026-09-26T11:00:00.000Z', 'end': '2026-09-26T15:00:00.000Z'},
        {'_id': 's3', 'start': None, 'end': None},
    ]
    LIEUX = {'p1': 'Lodève — entrée'}

    def creneaux(self, registrations):
        return creneaux_depuis_noe(self.SESSIONS, registrations, self.LIEUX)

    def test_conversion_en_heure_locale_et_nom_du_lieu(self):
        creneaux = self.creneaux([{
            'user': {'_id': 'u1', 'firstName': 'Paula', 'lastName': 'Martin'},
            'sessionsSubscriptions': [{'session': 's1'}],
        }])
        creneau_lu, = creneaux
        self.assertEqual(creneau_lu.nom, 'Paula Martin')
        self.assertEqual(creneau_lu.debut.hour, 8)        # 06:00 UTC = 08:00 à Paris
        self.assertEqual(creneau_lu.lieu, 'Lodève — entrée')

    def test_session_sans_lieu_et_session_sans_date(self):
        creneaux = self.creneaux([{
            'user': {'_id': 'u1', 'firstName': 'Paula', 'lastName': ''},
            'sessionsSubscriptions': [{'session': {'_id': 's2'}}, {'session': 's3'}],
        }])
        creneau_lu, = creneaux                            # s3 écartée, faute de dates
        self.assertEqual(creneau_lu.lieu, SANS_LIEU)

    def test_inscription_sans_creneau_ou_sans_compte(self):
        self.assertEqual(self.creneaux([
            {'user': {'_id': 'u2', 'firstName': 'Julie'}, 'sessionsSubscriptions': []},
            {'user': 'u3', 'sessionsSubscriptions': [{'session': 's1'}]},
        ]), [])


class TestTableau(unittest.TestCase):

    def test_les_jours_sortent_dans_l_ordre(self):
        jours = tableau([
            creneau('Paula', 9, 12, jour=date(2026, 9, 27)),
            creneau('Julie', 9, 12, jour=date(2026, 9, 26)),
        ])
        self.assertEqual(list(jours), [date(2026, 9, 26), date(2026, 9, 27)])


if __name__ == '__main__':
    unittest.main()
