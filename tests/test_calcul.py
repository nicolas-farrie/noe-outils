"""Tests du calcul des repas — données fictives, aucun appel réseau.

Lancement depuis la racine du dépôt :

    python3 -m unittest discover -s tests -t .

Règles en vigueur (15/09/2026) : matin 08:00–16:00 → déjeuner, soir 16:00–03:00 → dîner,
≥ 2 h dans la plage, heures arrondies au quart d'heure.
"""

import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta

from catering.calcul import (
    FUSEAU, SANS_LIEU, Creneau, creneaux_depuis_noe, heures_hors_plages, heures_par_jour,
    lignes_du_jour, releve_anomalies, tableau, totaux_du_jour, totaux_par_lieu,
)
from catering.regles import REGLES_BENEVOLES, Plage, Regles

JOUR = date(2026, 9, 26)
LENDEMAIN = date(2026, 9, 27)
DEJEUNER, DINER = REGLES_BENEVOLES.plages


def h(heures, minutes=0):
    """Une heure de la journée, en heures décimales : h(15, 16) → 15 h 16."""
    return heures + minutes / 60


def creneau(nom, debut, fin, lieu=SANS_LIEU, jour=JOUR):
    """Un créneau en heures depuis minuit du jour — au-delà de 24, le lendemain."""
    minuit = datetime.combine(jour, datetime.min.time(), tzinfo=FUSEAU)
    return Creneau(
        benevole=nom.lower(),
        nom=nom,
        debut=minuit + timedelta(minutes=round(debut * 60)),
        fin=minuit + timedelta(minutes=round(fin * 60)),
        lieu=lieu,
    )


def lignes(*creneaux, regles=REGLES_BENEVOLES, jour=JOUR):
    return lignes_du_jour(heures_par_jour(list(creneaux), regles)[jour], regles)


def repas(ligne, regles=REGLES_BENEVOLES):
    """(déjeuner, dîner) accordés à une ligne."""
    return tuple(ligne.a_droit(plage) for plage in regles.plages)


class TestPlages(unittest.TestCase):
    """Deux heures dans une plage ouvrent le repas de cette plage, où que tombe le créneau."""

    def test_apres_midi_avant_seize_heures_donne_le_dejeuner(self):
        ligne, = lignes(creneau('Paula', 14, 16))       # exemple de l'équipe
        self.assertEqual(ligne.heures, {'dejeuner': 2, 'diner': 0})
        self.assertEqual(repas(ligne), (True, False))

    def test_matinee(self):
        ligne, = lignes(creneau('Paula', 8, 10))
        self.assertEqual(repas(ligne), (True, False))

    def test_soiree(self):
        ligne, = lignes(creneau('Julie', 16, 18))
        self.assertEqual(repas(ligne), (False, True))

    def test_creneau_a_cheval_sur_seize_heures(self):
        ligne, = lignes(creneau('Julie', 15, 17))        # 1 h de chaque côté
        self.assertEqual(ligne.heures, {'dejeuner': 1, 'diner': 1})
        self.assertEqual(repas(ligne), (False, False))

    def test_deux_plages_deux_repas(self):
        ligne, = lignes(creneau('Claude', 10, 12), creneau('Claude', 18, 20))
        self.assertEqual(repas(ligne), (True, True))


class TestSoireeApresMinuit(unittest.TestCase):
    """Le soir court jusqu'à 03:00 ; ces heures appartiennent à la veille."""

    def test_soiree_qui_finit_a_deux_heures(self):
        presences = heures_par_jour([creneau('Julie', 22, 26)])     # 22:00 → 02:00
        self.assertEqual(list(presences), [JOUR])
        ligne, = lignes_du_jour(presences[JOUR])
        self.assertEqual(ligne.heures['diner'], 4)

    def test_creneau_de_nuit_rattache_a_la_veille(self):
        nuit = creneau('Julie', 0, 2, jour=LENDEMAIN)                # 00:00 → 02:00 le 27
        presences = heures_par_jour([nuit])
        self.assertEqual(list(presences), [JOUR])
        ligne, = lignes_du_jour(presences[JOUR])
        self.assertEqual((ligne.heures['diner'], repas(ligne)), (2, (False, True)))


class TestArrondi(unittest.TestCase):
    """Reste de 15 min ou moins : vers le bas ; au-delà : heure pleine suivante."""

    def test_une_heure_seize_compte_deux_heures(self):
        ligne, = lignes(creneau('Paula', 14, h(15, 16)))
        self.assertEqual(ligne.heures['dejeuner'], 2)
        self.assertTrue(ligne.a_droit(DEJEUNER))

    def test_une_heure_quinze_compte_une_heure(self):
        ligne, = lignes(creneau('Paula', 14, h(15, 15)))
        self.assertEqual(ligne.heures['dejeuner'], 1)
        self.assertFalse(ligne.a_droit(DEJEUNER))

    def test_arrondi_sur_le_total_de_la_plage_pas_par_creneau(self):
        # Quatre créneaux de 20 min : arrondis un par un, ils feraient 4 h. Le total de la
        # plage, 1 h 20, fait 2 h.
        ligne, = lignes(*(creneau('Claude', debut, h(debut, 20)) for debut in (9, 10, 11, 12)))
        self.assertEqual(ligne.heures['dejeuner'], 2)

    def test_sans_arrondi(self):
        exactes = Regles(REGLES_BENEVOLES.plages, tolerance_arrondi=None)
        ligne, = lignes(creneau('Paula', 14, h(15, 15)), regles=exactes)
        self.assertEqual(ligne.heures['dejeuner'], 1.25)

    def test_arrondir(self):
        arrondir = REGLES_BENEVOLES.arrondir
        self.assertEqual([arrondir(x) for x in (0, h(0, 15), h(0, 16), h(1, 45), 2)],
                         [0, 0, 1, 2, 2])


class TestAnomalies(unittest.TestCase):
    """Les heures entre 03:00 et 08:00 ne tombent dans aucune plage."""

    def test_creneau_commence_avant_huit_heures(self):
        c = creneau('Paula', 5, 9)
        self.assertEqual(heures_hors_plages(c.debut, c.fin, REGLES_BENEVOLES), 3)
        ligne, = lignes(c)
        self.assertEqual(ligne.heures['dejeuner'], 1)     # seule 08:00-09:00 compte

    def test_nuit_au_dela_de_trois_heures(self):
        c = creneau('Julie', 2, 4, jour=LENDEMAIN)          # 02:00 → 04:00
        self.assertEqual(heures_hors_plages(c.debut, c.fin, REGLES_BENEVOLES), 1)
        ligne, = lignes_du_jour(heures_par_jour([c])[JOUR])
        self.assertEqual(ligne.heures['diner'], 1)          # 02:00-03:00, pour la veille

    def test_releve(self):
        anomalies = releve_anomalies([creneau('Claude', 10, 12), creneau('Paula', 5, 9)])
        self.assertEqual([(a.creneau.nom, a.heures) for a in anomalies], [('Paula', 3)])


class TestLieux(unittest.TestCase):

    def test_repas_servi_au_lieu_de_la_plage(self):
        jour = lignes(creneau('Claude', 10, 12, lieu='A'), creneau('Claude', 18, 20, lieu='B'))
        self.assertEqual(totaux_par_lieu(jour),
                         {'A': {'dejeuner': 1, 'diner': 0}, 'B': {'dejeuner': 0, 'diner': 1}})

    def test_deux_lieux_dans_la_meme_plage_un_seul_repas(self):
        jour = lignes(creneau('Claude', 8, 11, lieu='A'), creneau('Claude', 12, 14, lieu='B'))
        self.assertEqual(totaux_du_jour(jour), {'dejeuner': 1, 'diner': 0})
        self.assertEqual(jour[0].lieux['dejeuner'], 'A')    # 3 h contre 2 h

    def test_lieux_a_egalite_le_premier_commence_l_emporte(self):
        jour = lignes(creneau('Julie', 8, 10, lieu='A'), creneau('Julie', 11, 13, lieu='B'))
        self.assertEqual(jour[0].lieux['dejeuner'], 'A')

    def test_creneaux_qui_se_chevauchent_comptes_une_fois(self):
        ligne, = lignes(creneau('Claude', 8, 12, lieu='A'), creneau('Claude', 10, 13, lieu='B'))
        self.assertEqual(ligne.heures['dejeuner'], 5)       # 08-13, pas 4 + 3
        self.assertEqual(ligne.lieux['dejeuner'], 'A')      # 4 h contre 1 h


class TestFenetreDeRepas(unittest.TestCase):
    """Écartée par l'équipe, gardée en paramètre : désactivée tant qu'elle vaut None ou 0."""

    def regles(self, presence):
        return replace(REGLES_BENEVOLES,
                       plages=(replace(DEJEUNER, presence_fenetre=presence), DINER))

    def test_desactivee_par_defaut(self):
        ligne, = lignes(creneau('Paula', h(12, 30), h(13, 30)))   # 1 h, dans 12-14
        self.assertFalse(ligne.a_droit(DEJEUNER))

    def test_active(self):
        regles = self.regles(0.5)
        ligne, = lignes(creneau('Paula', h(12, 30), h(13, 30)), regles=regles)
        self.assertEqual(ligne.fenetre['dejeuner'], 1)
        self.assertTrue(ligne.a_droit(regles.plages[0]))

    def test_zero_la_desactive(self):
        regles = self.regles(0)
        ligne, = lignes(creneau('Paula', h(12, 30), h(13, 30)), regles=regles)
        self.assertFalse(ligne.a_droit(regles.plages[0]))


class TestRegles(unittest.TestCase):

    def test_seuils_modifiables(self):
        severes = REGLES_BENEVOLES.avec_seuils(dejeuner=3)
        ligne, = lignes(creneau('Claude', 10, 12), regles=severes)
        self.assertFalse(ligne.a_droit(severes.plages[0]))

    def test_seuil_inconnu(self):
        with self.assertRaises(ValueError):
            REGLES_BENEVOLES.avec_seuils(gouter=1)

    def test_plage_incoherente(self):
        with self.assertRaises(ValueError):
            Plage('diner', 'Dîner', 'soir', debut=16, fin=8)
        with self.assertRaises(ValueError):
            Plage('diner', 'Dîner', 'soir', debut=16, fin=27, fenetre=(12, 14))

    def test_un_repas_par_plage(self):
        with self.assertRaises(ValueError):
            Regles((DEJEUNER, replace(DINER, repas='dejeuner')))

    def test_description(self):
        texte = REGLES_BENEVOLES.description()
        for attendu in ('déjeuner : ≥ 2 h entre 08:00 et 16:00',
                        'dîner : ≥ 2 h entre 16:00 et 03:00', 'arrondies'):
            self.assertIn(attendu, texte)


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
        creneau_lu, = self.creneaux([{
            'user': {'_id': 'u1', 'firstName': 'Paula', 'lastName': 'Martin'},
            'sessionsSubscriptions': [{'session': 's1'}],
        }])
        self.assertEqual(creneau_lu.nom, 'Paula Martin')
        self.assertEqual(creneau_lu.debut.hour, 8)        # 06:00 UTC = 08:00 à Paris
        self.assertEqual(creneau_lu.lieu, 'Lodève — entrée')

    def test_session_sans_lieu_et_session_sans_date(self):
        creneau_lu, = self.creneaux([{
            'user': {'_id': 'u1', 'firstName': 'Paula', 'lastName': ''},
            'sessionsSubscriptions': [{'session': {'_id': 's2'}}, {'session': 's3'}],
        }])                                               # s3 écartée, faute de dates
        self.assertEqual(creneau_lu.lieu, SANS_LIEU)

    def test_inscription_sans_creneau_ou_sans_compte(self):
        self.assertEqual(self.creneaux([
            {'user': {'_id': 'u2', 'firstName': 'Julie'}, 'sessionsSubscriptions': []},
            {'user': 'u3', 'sessionsSubscriptions': [{'session': 's1'}]},
        ]), [])


class TestTableau(unittest.TestCase):

    def test_les_jours_sortent_dans_l_ordre(self):
        jours = tableau([creneau('Paula', 9, 12, jour=LENDEMAIN), creneau('Julie', 9, 12)])
        self.assertEqual(list(jours), [JOUR, LENDEMAIN])


if __name__ == '__main__':
    unittest.main()
