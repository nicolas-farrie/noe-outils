"""Qui mange, quand, et sur quel lieu — calculé depuis les créneaux souscrits dans NOÉ.

Les règles (plages, seuils, arrondi) vivent dans `regles.py` ; ce module ne fait que les
appliquer. Pour chaque personne et chaque jour :

1. ses créneaux sont mis à plat, chevauchements déduits ;
2. chaque créneau est découpé sur les plages de la journée. Une plage peut déborder après
   minuit — le soir court jusqu'à 03:00 — et ces heures de nuit restent rattachées à la
   veille ;
3. les heures de chaque plage sont arrondies, puis comparées au seuil de la plage : le
   repas est accordé ou non ;
4. le repas est servi au lieu où la personne passe le plus d'heures dans la plage — une
   personne n'a qu'un repas par plage, même présente sur deux lieux.

Les heures qui ne tombent dans aucune plage (entre 03:00 et 08:00 pour les bénévoles) ne
comptent pour aucun repas : elles sont relevées comme anomalies. Pour l'équipe, ce sont
des erreurs de saisie ou des heures de nuit à vérifier dans NOÉ.

Module volontairement sans réseau ni dépendance : tout entre par `creneaux_depuis_noe()`,
qui prend les listes déjà lues par `NoeClient`.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .regles import PRECISION, REGLES_BENEVOLES

# L'API date les créneaux en UTC ; le découpage, lui, est une règle humaine (« le soir »),
# donc il se fait en heure locale — sinon tout est décalé de deux heures en septembre.
FUSEAU = ZoneInfo('Europe/Paris')

SANS_LIEU = '(sans lieu)'

EPSILON = 10 ** -(PRECISION + 2)


@dataclass(frozen=True)
class Creneau:
    """Une personne, un intervalle daté (heure locale), un lieu."""

    benevole: str          # identifiant NOÉ de la personne (stable d'un projet à l'autre)
    nom: str               # « Prénom Nom », pour le tableau remis à l'équipe
    debut: datetime
    fin: datetime
    lieu: str = SANS_LIEU


@dataclass(frozen=True)
class Anomalie:
    """Un créneau dont une partie ne tombe dans aucune plage."""

    creneau: Creneau
    heures: float          # heures hors de toute plage


@dataclass
class Presence:
    """Heures d'une personne sur une journée, par plage puis par lieu."""

    nom: str
    heures: dict = field(default_factory=lambda: defaultdict(dict))  # repas -> {lieu: heures}
    fenetre: dict = field(default_factory=dict)       # repas -> heures pendant le repas
    debut_lieu: dict = field(default_factory=dict)    # lieu -> premier créneau commencé


@dataclass
class Ligne:
    """Une ligne du tableau : une personne, un jour."""

    benevole: str
    nom: str
    heures: dict           # repas -> heures de la plage, arrondies
    fenetre: dict          # repas -> heures pendant le repas, exactes
    lieux: dict            # repas -> lieu où le repas est servi

    def a_droit(self, plage):
        """Le repas de cette plage est-il accordé ?"""
        heures = self.heures.get(plage.repas, 0)
        if heures > 0 and heures >= plage.seuil - EPSILON:
            return True
        return (plage.fenetre_active
                and self.fenetre.get(plage.repas, 0) >= plage.presence_fenetre - EPSILON)


def _ident(valeur):
    """L'id d'une référence, qu'elle soit peuplée ({_id: …}) ou brute ("…")."""
    return valeur.get('_id') if isinstance(valeur, dict) else valeur


def _horodatage(valeur):
    """Une date ISO de l'API (« 2026-09-26T08:00:00.000Z ») en heure locale."""
    horodate = datetime.fromisoformat(valeur.replace('Z', '+00:00'))
    if horodate.tzinfo is None:
        horodate = horodate.replace(tzinfo=timezone.utc)
    return horodate.astimezone(FUSEAU)


def creneaux_depuis_noe(sessions, registrations, lieux):
    """Les créneaux souscrits, à plat.

    Args:
        sessions: `NoeClient.list_sessions()`
        registrations: `NoeClient.list_registrations()` (user peuplé)
        lieux: {identifiant: nom} — `NoeClient.list_places()`

    Une inscription sans créneau ne produit rien : elle ne mange pas.
    """
    par_id = {s['_id']: s for s in sessions}
    creneaux = []

    for inscription in registrations:
        personne = inscription.get('user')
        if not isinstance(personne, dict):
            continue  # référence non peuplée : personne identifiable derrière
        nom = ' '.join(
            morceau for morceau in ((personne.get('firstName') or '').strip(),
                                    (personne.get('lastName') or '').strip()) if morceau)

        for souscription in (inscription.get('sessionsSubscriptions') or []):
            session = par_id.get(_ident(souscription.get('session')))
            if not session or not session.get('start') or not session.get('end'):
                continue
            # Une session peut porter plusieurs espaces ; on retient le premier — le cas ne
            # s'est pas présenté sur le festival.
            references = [_ident(lieu) for lieu in (session.get('places') or [])]
            lieu = lieux.get(references[0], SANS_LIEU) if references else SANS_LIEU
            creneaux.append(Creneau(
                benevole=personne.get('_id') or '',
                nom=nom or (personne.get('email') or '').strip(),
                debut=_horodatage(session['start']),
                fin=_horodatage(session['end']),
                lieu=lieu,
            ))

    return creneaux


def _instant(jour, heures):
    """L'instant local à `heures` h du jour J — au-delà de 24, le lendemain (27 → 03:00)."""
    jours, minutes = divmod(round(heures * 60), 24 * 60)
    return datetime.combine(jour + timedelta(days=jours),
                            time(minutes // 60, minutes % 60), tzinfo=FUSEAU)


def _chevauchement(debut, fin, borne_debut, borne_fin):
    """Heures communes à deux intervalles."""
    return max(0.0, (min(fin, borne_fin) - max(debut, borne_debut)).total_seconds() / 3600)


def _jours(debut, fin):
    """Les jours dont une plage peut toucher l'intervalle — à partir de la veille, puisqu'un
    créneau commencé à 01:00 appartient au soir du jour précédent."""
    jour = debut.date() - timedelta(days=1)
    while jour <= fin.date():
        yield jour
        jour += timedelta(days=1)


def _portions(debut, fin, regles):
    """(jour, plage, heures, heures pendant le repas) pour chaque plage touchée."""
    for jour in _jours(debut, fin):
        for plage in regles.plages:
            heures = _chevauchement(debut, fin, _instant(jour, plage.debut),
                                    _instant(jour, plage.fin))
            if heures <= 0:
                continue
            pendant_repas = (_chevauchement(debut, fin, _instant(jour, plage.fenetre[0]),
                                            _instant(jour, plage.fenetre[1]))
                             if plage.fenetre else 0.0)
            yield jour, plage, heures, pendant_repas


def heures_hors_plages(debut, fin, regles):
    """Heures de l'intervalle qui ne tombent dans aucune plage."""
    bornes = sorted((_instant(jour, plage.debut), _instant(jour, plage.fin))
                    for jour in _jours(debut, fin) for plage in regles.plages)
    couvertes, curseur = 0.0, debut
    for borne_debut, borne_fin in bornes:   # les plages peuvent se chevaucher : on les unit
        depart, arrivee = max(borne_debut, curseur), min(borne_fin, fin)
        if arrivee > depart:
            couvertes += (arrivee - depart).total_seconds() / 3600
            curseur = arrivee
    return round((fin - debut).total_seconds() / 3600 - couvertes, PRECISION)


def releve_anomalies(creneaux, regles=REGLES_BENEVOLES):
    """Les créneaux qui débordent des plages, dans l'ordre chronologique."""
    anomalies = []
    for creneau in sorted(creneaux, key=lambda c: (c.debut, c.nom)):
        heures = heures_hors_plages(creneau.debut, creneau.fin, regles)
        if heures > 0:
            anomalies.append(Anomalie(creneau, heures))
    return anomalies


def heures_par_jour(creneaux, regles=REGLES_BENEVOLES):
    """{jour: {personne: Presence}} — chevauchements déduits.

    Une personne inscrite à deux créneaux qui se recouvrent n'est présente qu'une fois :
    la partie commune revient au créneau commencé le premier, et donc à son lieu.
    """
    presences = defaultdict(dict)
    par_benevole = defaultdict(list)
    for creneau in creneaux:
        par_benevole[creneau.benevole].append(creneau)

    for benevole, liste in par_benevole.items():
        curseur = None   # fin du dernier créneau déjà compté
        for creneau in sorted(liste, key=lambda c: (c.debut, c.fin)):
            debut = creneau.debut if curseur is None else max(creneau.debut, curseur)
            if debut >= creneau.fin:
                continue  # entièrement recouvert par un créneau précédent
            curseur = creneau.fin if curseur is None else max(curseur, creneau.fin)

            for jour, plage, heures, pendant_repas in _portions(debut, creneau.fin, regles):
                presence = presences[jour].setdefault(benevole, Presence(creneau.nom))
                par_lieu = presence.heures[plage.repas]
                par_lieu[creneau.lieu] = round(par_lieu.get(creneau.lieu, 0.0) + heures,
                                               PRECISION)
                if pendant_repas:
                    presence.fenetre[plage.repas] = round(
                        presence.fenetre.get(plage.repas, 0.0) + pendant_repas, PRECISION)
                presence.debut_lieu.setdefault(creneau.lieu, creneau.debut)

    return presences


def _lieu_dominant(par_lieu, presence):
    """Le lieu où la personne passe le plus d'heures ; à égalité, le premier commencé.

    Plage vide (cas d'un seuil à zéro, ou d'une fenêtre de repas active) : on retient le
    lieu dominant de la journée, pour que le repas ne disparaisse pas du décompte.
    """
    if not par_lieu:
        par_lieu = defaultdict(float)
        for heures_plage in presence.heures.values():
            for lieu, heures in heures_plage.items():
                par_lieu[lieu] += heures
    if not par_lieu:
        return SANS_LIEU
    return min(par_lieu, key=lambda lieu: (-par_lieu[lieu], presence.debut_lieu.get(lieu)))


def lignes_du_jour(presences_du_jour, regles=REGLES_BENEVOLES):
    """Une ligne par personne présente ce jour-là, triée par nom."""
    lignes = []
    for benevole, presence in presences_du_jour.items():
        heures, lieux = {}, {}
        for plage in regles.plages:
            par_lieu = presence.heures.get(plage.repas, {})
            heures[plage.repas] = regles.arrondir(sum(par_lieu.values()))
            lieux[plage.repas] = _lieu_dominant(par_lieu, presence)
        lignes.append(Ligne(benevole, presence.nom, heures, dict(presence.fenetre), lieux))
    return sorted(lignes, key=lambda ligne: (ligne.nom, ligne.benevole))


def tableau(creneaux, regles=REGLES_BENEVOLES):
    """{jour: [Ligne]} — le tableau complet, jours dans l'ordre."""
    presences = heures_par_jour(creneaux, regles)
    return {jour: lignes_du_jour(presences[jour], regles) for jour in sorted(presences)}


def totaux_par_lieu(lignes, regles=REGLES_BENEVOLES):
    """{lieu: {repas: nombre}} pour une journée — seuls les lieux où l'on mange."""
    totaux = {}
    for ligne in lignes:
        for plage in regles.plages:
            if ligne.a_droit(plage):
                compte = totaux.setdefault(ligne.lieux[plage.repas],
                                           {p.repas: 0 for p in regles.plages})
                compte[plage.repas] += 1
    return dict(sorted(totaux.items()))


def totaux_du_jour(lignes, regles=REGLES_BENEVOLES):
    """{repas: nombre} pour une journée, tous lieux confondus."""
    return {plage.repas: sum(1 for ligne in lignes if ligne.a_droit(plage))
            for plage in regles.plages}
