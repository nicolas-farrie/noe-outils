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
import sys
from collections import Counter

from .calcul import (creneaux_depuis_noe, creneaux_encadrement, encadrements_sans_espace,
                     releve_anomalies, tableau, totaux_du_jour, totaux_par_lieu)
from .regimes import (REGIMES, champ_regime, profils_alimentaires, repartition, signalements,
                      tags_utilises)
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
    """(créneaux, profils alimentaires, tags utilisés, lectures) — les listes de l'API,
    croisées côté client.

    Les filtres de NOÉ ne traversent pas les références : on lit tout, on croise ici.
    """
    lieux = {lieu['_id']: lieu.get('name') or '(sans nom)' for lieu in client.list_places()}
    sessions = client.list_sessions()
    inscriptions = client.list_registrations()
    fiches = client.list_stewards()
    benevolat = creneaux_depuis_noe(sessions, inscriptions, lieux)
    encadrement = creneaux_encadrement(sessions, inscriptions, fiches, lieux)

    cle_regime = champ_regime(client.form_fields())
    if cle_regime is None:
        # Le formulaire a changé : on sert quand même les repas, sans les régimes, plutôt
        # que de laisser l'équipe sans tableau.
        print('Attention : champ « régime alimentaire » introuvable dans le formulaire NOÉ, '
              'classeur produit sans les régimes.', file=sys.stderr)
        profils = None
    else:
        profils = profils_alimentaires(inscriptions, cle_regime)

    return benevolat + encadrement, profils, tags_utilises(inscriptions), {
        'lieux': len(lieux), 'sessions': len(sessions), 'inscriptions': len(inscriptions),
        'créneaux souscrits': len(benevolat), 'encadrants': len(fiches),
        'encadrements': len(encadrement)}


def _comptes(totaux, regles):
    return ', '.join(f'{totaux[p.repas]} {p.nom_repas.lower()}(s)' for p in regles.plages)


def _ligne_tags(tags_noe, regimes):
    """Tous les tags posés dans NOÉ — pour repérer un tag qui ne serait pas alimentaire."""
    if not tags_noe:
        return 'Tags utilisés dans NOÉ : aucun'
    liste = ', '.join(f'{tag} {nombre}' for tag, nombre in sorted(tags_noe.items()))
    if regimes.tags_exceptions is None:
        return f'Tags utilisés dans NOÉ (tous pris comme exceptions alimentaires) : {liste}'
    ecartes = sorted(tag for tag in tags_noe if not regimes.exceptions([tag]))
    return (f'Tags utilisés dans NOÉ : {liste}'
            + (f' — non retenus comme exceptions : {", ".join(ecartes)}' if ecartes else ''))


def resume(jours, regles=REGLES_BENEVOLES, anomalies=(), sans_espace=0, profils=None,
           regimes=REGIMES, tags_noe=None):
    """Le rapport de contrôle, en lignes de texte : des comptages, jamais de noms."""
    lignes_rapport = []
    cumul = {plage.repas: 0 for plage in regles.plages}

    for jour, lignes in jours.items():
        totaux = totaux_du_jour(lignes, regles)
        for repas, nombre in totaux.items():
            cumul[repas] += nombre
        encadrants = sum(1 for ligne in lignes if any(ligne.encadre.values()))
        lignes_rapport.append(f'{jour:%d/%m/%Y} — {len(lignes)} présent(s) dont '
                              f'{encadrants} encadrant(s), ' + _comptes(totaux, regles))
        for lieu, compte in totaux_par_lieu(lignes, regles).items():
            lignes_rapport.append(f'    {lieu[:34]:<34}' + ''.join(
                f' {compte[p.repas]:>3} {p.nom_repas[:3].lower()}.' for p in regles.plages))

    lignes_rapport.append(f'Total : {len(jours)} journée(s), ' + _comptes(cumul, regles))
    if sans_espace:
        lignes_rapport.append(f'Encadrements sans espace, sans repas : {sans_espace}')
    if profils is not None:
        categories = Counter()
        for ligne in repartition(jours, profils, regles, regimes):
            categories.update(ligne.categories)
        lignes_rapport.append('Régimes (repas accordés) : ' + ', '.join(
            f'{categorie} {categories[categorie]}' for categorie in regimes.colonnes()))
        tags = Counter(tag for signalement in signalements(jours, profils, regles)
                       for tag in signalement.exceptions)
        if tags:
            lignes_rapport.append('Exceptions (repas accordés) : ' + ', '.join(
                f'{tag} {nombre}' for tag, nombre in sorted(tags.items())))
    if tags_noe is not None:
        lignes_rapport.append(_ligne_tags(tags_noe, regimes))
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
    creneaux, profils, tags_noe, lectures = lire(client_depuis_environnement())
    jours = tableau(creneaux, regles)
    anomalies = releve_anomalies(creneaux, regles)

    print(', '.join(f'{valeur} {nom}' for nom, valeur in lectures.items()))
    for ligne in resume(jours, regles, anomalies, encadrements_sans_espace(creneaux), profils,
                        tags_noe=tags_noe):
        print(ligne)

    if options.out:
        # Import tardif : le rapport seul n'a pas besoin du générateur de classeur.
        from .ods import ecrire_classeur
        ecrire_classeur(options.out, jours, regles, anomalies, profils=profils)
        print(f'Classeur écrit : {options.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
