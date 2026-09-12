"""Qui mange, quand, et sur quel site — calculé depuis les créneaux souscrits dans NOÉ.

Règles (`catering/README.md`, confirmées par l'équipe du festival le 11/09/2026). Par
bénévole et par jour, en heure locale :

    AM   = heures de créneau entre 08:00 et 13:00
    PM   = heures de créneau entre 13:00 et minuit
    JOUR = AM + PM, tous lieux confondus

    déjeuner  si AM >= nbHeureAM  ou  JOUR >= nbHeureJour
    dîner     si PM >= nbHeurePM  ou  JOUR >= nbHeureJour

Le total de la journée l'emporte sur les demi-journées : 1 h le matin puis 5 h
l'après-midi ouvrent droit aux deux repas, alors qu'aucun des deux seuils de demi-journée
n'est atteint.

Les repas étant livrés sur plusieurs sites, chacun est rattaché au lieu où la personne
passe le plus d'heures dans la demi-journée concernée. Le droit au repas se décide sur la
journée entière, le lieu seulement après : la somme des lieux vaut toujours le total du
jour, aucun repas ne se perd en changeant de site.

Module volontairement sans réseau ni dépendance : tout entre par `creneaux_depuis_noe()`,
qui prend les listes déjà lues par `NoeClient`.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

# L'API date les créneaux en UTC ; le découpage, lui, est une règle humaine (« le matin »),
# donc il se fait en heure locale — sinon tout est décalé de deux heures en septembre.
FUSEAU = ZoneInfo('Europe/Paris')

# Les deux plages de la journée. La nuit, de minuit à 08:00, ne compte pas : aucun repas
# n'y est servi.
PLAGES = (('am', 8, 13), ('pm', 13, 24))

SANS_LIEU = '(sans lieu)'

# Les heures sont arrondies au dix-millième (0,36 s) avant toute comparaison : sans cela,
# une somme de flottants peut rendre 5,999999 pour six heures pleines et faire sauter un
# repas. Même arrondi que celui écrit dans le tableur, pour que ses formules retrouvent
# exactement les valeurs calculées ici.
PRECISION = 4
EPSILON = 10 ** -(PRECISION + 2)


@dataclass(frozen=True)
class Seuils:
    """Les trois variables du tableau, en heures."""

    am: float = 2.0
    pm: float = 4.0
    jour: float = 6.0


SEUILS_DEFAUT = Seuils()


@dataclass(frozen=True)
class Creneau:
    """Un bénévole, un intervalle daté (heure locale), un lieu."""

    benevole: str          # identifiant NOÉ de la personne (stable d'un projet à l'autre)
    nom: str               # « Prénom Nom », pour le tableau remis à l'équipe
    debut: datetime
    fin: datetime
    lieu: str = SANS_LIEU


@dataclass
class Presence:
    """Heures d'un bénévole sur une journée, ventilées par plage et par lieu."""

    nom: str
    am: dict = field(default_factory=dict)           # lieu -> heures
    pm: dict = field(default_factory=dict)           # lieu -> heures
    debut_lieu: dict = field(default_factory=dict)   # lieu -> premier créneau commencé


@dataclass
class Ligne:
    """Une ligne du tableau : un bénévole, un jour."""

    benevole: str
    nom: str
    am: float
    pm: float
    lieu_matin: str
    lieu_soir: str

    @property
    def jour(self):
        return round(self.am + self.pm, PRECISION)

    def dejeuner(self, seuils=SEUILS_DEFAUT):
        return self.am >= seuils.am - EPSILON or self.jour >= seuils.jour - EPSILON

    def diner(self, seuils=SEUILS_DEFAUT):
        return self.pm >= seuils.pm - EPSILON or self.jour >= seuils.jour - EPSILON


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
            # Une session peut porter plusieurs lieux ; on retient le premier, comme le
            # prototype du 10/09 — le cas ne s'est pas présenté sur le festival.
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


def _borne(jour, heure):
    """L'instant local `jour` à `heure` h — 24 désignant minuit au jour suivant."""
    if heure >= 24:
        return datetime.combine(jour + timedelta(days=1), time(0), tzinfo=FUSEAU)
    return datetime.combine(jour, time(heure), tzinfo=FUSEAU)


def _decoupe(debut, fin):
    """(jour, plage, heures) pour chaque portion utile de l'intervalle.

    Un créneau de 11 h à 15 h donne 2 h le matin et 2 h l'après-midi ; un créneau de 22 h
    à 2 h donne 2 h le soir et rien le lendemain, la nuit ne comptant pas.
    """
    jour = debut.date()
    while jour <= fin.date():
        for plage, depart, arrivee in PLAGES:
            duree = (min(fin, _borne(jour, arrivee))
                     - max(debut, _borne(jour, depart))).total_seconds() / 3600
            if duree > 0:
                yield jour, plage, round(duree, PRECISION)
        jour += timedelta(days=1)


def heures_par_jour(creneaux):
    """{jour: {bénévole: Presence}} — chevauchements déduits.

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

            for jour, plage, duree in _decoupe(debut, creneau.fin):
                presence = presences[jour].setdefault(benevole, Presence(creneau.nom))
                cumul = presence.am if plage == 'am' else presence.pm
                cumul[creneau.lieu] = round(cumul.get(creneau.lieu, 0.0) + duree, PRECISION)
                presence.debut_lieu.setdefault(creneau.lieu, creneau.debut)

    return presences


def _lieu_dominant(cumul, presence):
    """Le lieu où la personne passe le plus d'heures ; à égalité, le premier commencé.

    Demi-journée vide — six heures d'affilée le matin ouvrent quand même droit au dîner :
    on retient alors le lieu dominant de la journée, pour que le repas ne disparaisse pas
    du décompte d'un site.
    """
    if not cumul:
        cumul = {lieu: presence.am.get(lieu, 0.0) + presence.pm.get(lieu, 0.0)
                 for lieu in set(presence.am) | set(presence.pm)}
    if not cumul:
        return SANS_LIEU
    return min(cumul, key=lambda lieu: (-cumul[lieu], presence.debut_lieu.get(lieu)))


def lignes_du_jour(presences_du_jour):
    """Une ligne par bénévole présent ce jour-là, triée par nom."""
    lignes = []
    for benevole, presence in presences_du_jour.items():
        lignes.append(Ligne(
            benevole=benevole,
            nom=presence.nom,
            am=round(sum(presence.am.values()), PRECISION),
            pm=round(sum(presence.pm.values()), PRECISION),
            lieu_matin=_lieu_dominant(presence.am, presence),
            lieu_soir=_lieu_dominant(presence.pm, presence),
        ))
    return sorted(lignes, key=lambda ligne: (ligne.nom, ligne.benevole))


def tableau(creneaux):
    """{jour: [Ligne]} — le tableau complet, jours dans l'ordre."""
    presences = heures_par_jour(creneaux)
    return {jour: lignes_du_jour(presences[jour]) for jour in sorted(presences)}


def totaux_par_lieu(lignes, seuils=SEUILS_DEFAUT):
    """{lieu: (déjeuners, dîners)} pour une journée."""
    totaux = defaultdict(lambda: [0, 0])
    for ligne in lignes:
        if ligne.dejeuner(seuils):
            totaux[ligne.lieu_matin][0] += 1
        if ligne.diner(seuils):
            totaux[ligne.lieu_soir][1] += 1
    return {lieu: tuple(compte) for lieu, compte in sorted(totaux.items())}


def totaux_du_jour(lignes, seuils=SEUILS_DEFAUT):
    """(déjeuners, dîners) pour une journée, tous lieux confondus."""
    return (sum(1 for ligne in lignes if ligne.dejeuner(seuils)),
            sum(1 for ligne in lignes if ligne.diner(seuils)))
