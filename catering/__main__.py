"""Repas à prévoir — lecture de NOÉ, comptage, rapport.

    set -a; . ./.env; set +a          # le code ne lit pas .env lui-même
    python3 -m catering --dry-run

`--dry-run` n'affiche que des comptages, par jour et par lieu : ce sont des données
personnelles réelles, aucune liste nominative ne sort en session (cf. CLAUDE.md). Les
noms ne figurent que dans le fichier remis à l'équipe.
"""

import argparse
import os

from .calcul import (SEUILS_DEFAUT, Seuils, creneaux_depuis_noe, tableau,
                     totaux_du_jour, totaux_par_lieu)

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


def resume(jours, seuils=SEUILS_DEFAUT):
    """Le rapport de contrôle, en lignes de texte : des comptages, jamais de noms."""
    lignes_rapport = []
    total_dejeuners = total_diners = 0

    for jour, lignes in jours.items():
        dejeuners, diners = totaux_du_jour(lignes, seuils)
        total_dejeuners += dejeuners
        total_diners += diners
        lignes_rapport.append(
            f'{jour:%d/%m/%Y} — {len(lignes)} bénévole(s) présent(s), '
            f'{dejeuners} déjeuner(s), {diners} dîner(s)')
        for lieu, (dejeuners_lieu, diners_lieu) in totaux_par_lieu(lignes, seuils).items():
            lignes_rapport.append(
                f'    {lieu[:34]:<34} {dejeuners_lieu:>3} déj. {diners_lieu:>3} dîn.')

    lignes_rapport.append(
        f'Total : {len(jours)} journée(s), {total_dejeuners} déjeuner(s), '
        f'{total_diners} dîner(s)')
    return lignes_rapport


def main(arguments=None):
    analyseur = argparse.ArgumentParser(
        prog='python3 -m catering',
        description='Repas à prévoir pour les bénévoles, depuis les créneaux NOÉ.')
    analyseur.add_argument('--dry-run', action='store_true',
                           help='afficher les comptages sans rien écrire ni déposer')
    analyseur.add_argument('--out', metavar='FICHIER',
                           help='écrire le classeur .ods dans ce fichier')
    analyseur.add_argument('--am', type=float, default=SEUILS_DEFAUT.am,
                           metavar='H', help='heures du matin ouvrant le déjeuner (défaut : %(default)s)')
    analyseur.add_argument('--pm', type=float, default=SEUILS_DEFAUT.pm,
                           metavar='H', help='heures de l\'après-midi ouvrant le dîner (défaut : %(default)s)')
    analyseur.add_argument('--jour', type=float, default=SEUILS_DEFAUT.jour,
                           metavar='H', help='heures dans la journée ouvrant les deux repas (défaut : %(default)s)')
    options = analyseur.parse_args(arguments)

    if not (options.dry_run or options.out):
        analyseur.error('rien à faire : choisir --dry-run et/ou --out '
                        '(le dépôt Seafile est à venir)')

    seuils = Seuils(options.am, options.pm, options.jour)
    creneaux, lectures = lire(client_depuis_environnement())
    jours = tableau(creneaux)

    print(', '.join(f'{valeur} {nom}' for nom, valeur in lectures.items()))
    for ligne in resume(jours, seuils):
        print(ligne)

    if options.out:
        # Import tardif : le rapport seul n'a pas besoin du générateur de classeur.
        from .ods import ecrire_classeur
        ecrire_classeur(options.out, jours, seuils)
        print(f'Classeur écrit : {options.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
