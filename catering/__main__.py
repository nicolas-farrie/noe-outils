"""Repas à prévoir — lecture de NOÉ, comptage, rapport, classeur.

    set -a; . ./.env; set +a          # le code ne lit pas .env lui-même
    python3 -m catering --dry-run
    python3 -m catering --out out/repas.ods

`--dry-run` n'affiche que des comptages, par jour et par lieu : ce sont des données
personnelles réelles, aucune liste nominative ne sort en session (cf. CLAUDE.md). Les
noms ne figurent que dans le classeur remis à l'équipe.
"""

import argparse
import os

from .calcul import (creneaux_depuis_noe, releve_anomalies, tableau, totaux_du_jour,
                     totaux_par_lieu)
from .regles import REGLES_BENEVOLES

VARIABLES = ('NOE_URL', 'NOE_TOKEN', 'NOE_PROJECT_ID')


def client_depuis_environnement():
    """Le client NOÉ, configuré par l'environnement."""
    # Import tardif : `noe` dépend de `requests`, dont le calcul et ses tests se passent.
    from noe import NoeClient

    manquantes = [nom for nom in VARIABLES if not os.environ.get(nom)]
    if manquantes:
        raise SystemExit(
            'Variables d\'environnement manquantes : ' + ', '.join(manquantes)
            + '\nLancer d\'abord :  set -a; . ./.env; set +a')
    return NoeClient(os.environ['NOE_URL'], os.environ['NOE_TOKEN'],
                     os.environ['NOE_PROJECT_ID'])


def lire(client):
    """(créneaux, lectures) — les trois listes de l'API, croisées côté client.

    Les filtres de NOÉ ne traversent pas les références : on lit tout, on croise ici.
    """
    lieux = {lieu['_id']: lieu.get('name') or '(sans nom)' for lieu in client.list_places()}
    sessions = client.list_sessions()
    inscriptions = client.list_registrations()
    creneaux = creneaux_depuis_noe(sessions, inscriptions, lieux)
    return creneaux, {'lieux': len(lieux), 'sessions': len(sessions),
                      'inscriptions': len(inscriptions), 'créneaux souscrits': len(creneaux)}


def _comptes(totaux, regles):
    return ', '.join(f'{totaux[p.repas]} {p.nom_repas.lower()}(s)' for p in regles.plages)


def resume(jours, regles=REGLES_BENEVOLES, anomalies=()):
    """Le rapport de contrôle, en lignes de texte : des comptages, jamais de noms."""
    lignes_rapport = []
    cumul = {plage.repas: 0 for plage in regles.plages}

    for jour, lignes in jours.items():
        totaux = totaux_du_jour(lignes, regles)
        for repas, nombre in totaux.items():
            cumul[repas] += nombre
        lignes_rapport.append(f'{jour:%d/%m/%Y} — {len(lignes)} bénévole(s) présent(s), '
                              + _comptes(totaux, regles))
        for lieu, compte in totaux_par_lieu(lignes, regles).items():
            lignes_rapport.append(f'    {lieu[:34]:<34}' + ''.join(
                f' {compte[p.repas]:>3} {p.nom_repas[:3].lower()}.' for p in regles.plages))

    lignes_rapport.append(f'Total : {len(jours)} journée(s), ' + _comptes(cumul, regles))
    if anomalies:
        heures = round(sum(anomalie.heures for anomalie in anomalies), 2)
        lignes_rapport.append(
            f'Anomalies : {len(anomalies)} créneau(x), {heures:g} h hors des plages '
            'horaires — à vérifier dans NOÉ')
    return lignes_rapport


def main(arguments=None):
    plages = {plage.repas: plage for plage in REGLES_BENEVOLES.plages}
    analyseur = argparse.ArgumentParser(
        prog='python3 -m catering',
        description='Repas à prévoir pour les bénévoles, depuis les créneaux NOÉ.')
    analyseur.add_argument('--dry-run', action='store_true',
                           help='afficher les comptages sans rien écrire ni déposer')
    analyseur.add_argument('--out', metavar='FICHIER',
                           help='écrire le classeur .ods dans ce fichier')
    analyseur.add_argument('--seuil-dejeuner', type=float, default=plages['dejeuner'].seuil,
                           metavar='H', help='heures du matin ouvrant le déjeuner '
                                             '(défaut : %(default)s)')
    analyseur.add_argument('--seuil-diner', type=float, default=plages['diner'].seuil,
                           metavar='H', help='heures du soir ouvrant le dîner '
                                             '(défaut : %(default)s)')
    options = analyseur.parse_args(arguments)

    if not (options.dry_run or options.out):
        analyseur.error('rien à faire : choisir --dry-run et/ou --out '
                        '(le dépôt Seafile est à venir)')

    regles = REGLES_BENEVOLES.avec_seuils(dejeuner=options.seuil_dejeuner,
                                          diner=options.seuil_diner)
    creneaux, lectures = lire(client_depuis_environnement())
    jours = tableau(creneaux, regles)
    anomalies = releve_anomalies(creneaux, regles)

    print(', '.join(f'{valeur} {nom}' for nom, valeur in lectures.items()))
    for ligne in resume(jours, regles, anomalies):
        print(ligne)

    if options.out:
        # Import tardif : le rapport seul n'a pas besoin du générateur de classeur.
        from .ods import ecrire_classeur
        ecrire_classeur(options.out, jours, regles, anomalies)
        print(f'Classeur écrit : {options.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
