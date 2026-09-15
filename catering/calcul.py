"""Qui mange, quand, et sur quel lieu — calculé depuis les créneaux NOÉ.

Les règles (plages, seuils, arrondi) vivent dans `regles.py` ; ce module ne fait que les
appliquer. Deux populations, deux jeux de règles :

- **bénévoles** : les sessions souscrites ; le repas d'une plage demande un minimum
  d'heures dans cette plage ;
- **encadrants** : les sessions où ils sont affectés (`session.stewards`) ; le repas de
  chaque plage touchée, quelle que soit la durée — mais seulement sur un espace : une
  session encadrée sans espace (téléphone, back-office) ne nourrit personne.

Pour chaque personne et chaque jour :

1. ses créneaux sont mis à plat, chevauchements déduits ;
2. chaque créneau est découpé sur les plages de la journée. Une plage peut déborder après
   minuit — le soir court jusqu'à 03:00 — et ces heures restent rattachées à la veille ;
3. le repas d'une plage est accordé si la personne y a droit comme bénévole **ou** comme
   encadrant ;
4. il est servi au lieu où elle passe le plus d'heures dans la plage — une personne n'a
   qu'un repas par plage, même présente sur deux lieux ou sous deux casquettes.

Un encadrant relié à une inscription (`registration.steward`) est reconnu comme la personne
inscrite : c'est ce qui l'empêche de manger deux fois. Non relié, il reste une personne à
part — aucun rapprochement par le nom, source d'erreurs.

Les heures qui ne tombent dans aucune plage (entre 03:00 et 08:00) ne comptent pour aucun
repas : elles sont relevées comme anomalies, à vérifier dans NOÉ.

Module volontairement sans réseau ni dépendance : tout entre par `creneaux_depuis_noe()` et
`creneaux_encadrement()`, qui prennent les listes déjà lues par `NoeClient`.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .regles import PRECISION, REGLES_BENEVOLES, REGLES_ENCADRANTS

# L'API date les créneaux en UTC ; le découpage, lui, est une règle humaine (« le soir »),
# donc il se fait en heure locale — sinon tout est décalé de deux heures en septembre.
FUSEAU = ZoneInfo('Europe/Paris')

SANS_LIEU = '(sans lieu)'

BENEVOLE = 'bénévole'
ENCADRANT = 'encadrant'

EPSILON = 10 ** -(PRECISION + 2)


@dataclass(frozen=True)
class Creneau:
    """Une personne, un intervalle daté (heure locale), un lieu, une casquette."""

    benevole: str          # identifiant de la personne : compte NOÉ, ou « encadrant:<fiche> »
    nom: str               # « Prénom Nom », pour le tableau remis à l'équipe
    debut: datetime
    fin: datetime
    lieu: str = SANS_LIEU
    role: str = BENEVOLE


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
    heures: dict           # repas -> heures de bénévolat dans la plage, arrondies
    fenetre: dict          # repas -> heures pendant le repas, exactes
    lieux: dict            # repas -> lieu où le repas est servi
    encadre: dict = field(default_factory=dict)   # repas -> encadre une session de la plage

    def a_droit(self, plage):
        """Le repas de cette plage est-il accordé ?"""
        if self.encadre.get(plage.repas):
            return True
        heures = self.heures.get(plage.repas, 0)
        if heures > 0 and heures >= plage.seuil - EPSILON:
            return True
        return (plage.fenetre_active
                and self.fenetre.get(plage.repas, 0) >= plage.presence_fenetre - EPSILON)


# === Lecture des données NOÉ ===

def _ident(valeur):
    """L'id d'une référence, qu'elle soit peuplée ({_id: …}) ou brute ("…")."""
    return valeur.get('_id') if isinstance(valeur, dict) else valeur


def _horodatage(valeur):
    """Une date ISO de l'API (« 2026-09-26T08:00:00.000Z ») en heure locale."""
    horodate = datetime.fromisoformat(valeur.replace('Z', '+00:00'))
    if horodate.tzinfo is None:
        horodate = horodate.replace(tzinfo=timezone.utc)
    return horodate.astimezone(FUSEAU)


def _nom(fiche):
    """« Prénom Nom » d'un compte ou d'une fiche encadrant."""
    if not isinstance(fiche, dict):
        return ''
    return ' '.join(morceau for morceau in ((fiche.get('firstName') or '').strip(),
                                            (fiche.get('lastName') or '').strip()) if morceau)


def _lieu(session, lieux):
    """Le lieu d'une session. Une session peut porter plusieurs espaces ; on retient le
    premier — le cas ne s'est pas présenté sur le festival."""
    references = [_ident(lieu) for lieu in (session.get('places') or [])]
    return lieux.get(references[0], SANS_LIEU) if references else SANS_LIEU


def creneaux_depuis_noe(sessions, registrations, lieux):
    """Les créneaux souscrits par les bénévoles, à plat.

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
        nom = _nom(personne) or (personne.get('email') or '').strip()

        for souscription in (inscription.get('sessionsSubscriptions') or []):
            session = par_id.get(_ident(souscription.get('session')))
            if not session or not session.get('start') or not session.get('end'):
                continue
            creneaux.append(Creneau(
                benevole=personne.get('_id') or '',
                nom=nom,
                debut=_horodatage(session['start']),
                fin=_horodatage(session['end']),
                lieu=_lieu(session, lieux),
            ))

    return creneaux


def creneaux_encadrement(sessions, registrations, stewards, lieux):
    """Les sessions encadrées, à plat : un créneau par encadrant affecté à une session.

    Seul compte l'encadrant affecté à la session (`session.stewards`) : ceux de l'activité
    ne sont que « éligibles ». Un encadrant relié à une inscription (`registration.steward`)
    reçoit l'identité de la personne inscrite, pour ne pas manger deux fois s'il est aussi
    bénévole.

    Args:
        sessions, registrations, lieux: comme `creneaux_depuis_noe()`
        stewards: `NoeClient.list_stewards()` — pour les noms, quand la session ne porte
                  que des identifiants
    """
    fiches = {fiche['_id']: fiche for fiche in stewards}
    personne_de = {}   # fiche encadrant -> (identifiant, nom) de la personne inscrite
    for inscription in registrations:
        personne = inscription.get('user')
        if inscription.get('steward') and isinstance(personne, dict):
            personne_de[_ident(inscription['steward'])] = (
                personne.get('_id') or '', _nom(personne) or (personne.get('email') or ''))

    creneaux = []
    for session in sessions:
        if not session.get('stewards') or not session.get('start') or not session.get('end'):
            continue
        for reference in session['stewards']:
            fiche_id = _ident(reference)
            fiche = fiches.get(fiche_id) or (reference if isinstance(reference, dict) else {})
            identifiant, nom = personne_de.get(fiche_id, (f'encadrant:{fiche_id}', _nom(fiche)))
            creneaux.append(Creneau(
                benevole=identifiant,
                nom=nom or '(encadrant sans nom)',
                debut=_horodatage(session['start']),
                fin=_horodatage(session['end']),
                lieu=_lieu(session, lieux),
                role=ENCADRANT,
            ))
    return creneaux


def encadrements_sans_espace(creneaux):
    """Nombre de créneaux d'encadrement sans espace — hors site, donc sans repas."""
    return sum(1 for c in creneaux if c.role == ENCADRANT and c.lieu == SANS_LIEU)


# === Découpage sur les plages ===

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


def releve_anomalies(creneaux, regles=REGLES_BENEVOLES, regles_encadrants=REGLES_ENCADRANTS):
    """Les créneaux qui débordent des plages de leur population, dans l'ordre chronologique."""
    anomalies = []
    for creneau in sorted(creneaux, key=lambda c: (c.debut, c.nom)):
        applicables = regles_encadrants if creneau.role == ENCADRANT else regles
        heures = heures_hors_plages(creneau.debut, creneau.fin, applicables)
        if heures > 0:
            anomalies.append(Anomalie(creneau, heures))
    return anomalies


def heures_par_jour(creneaux, regles=REGLES_BENEVOLES):
    """{jour: {personne: Presence}} pour une population — chevauchements déduits.

    Une personne inscrite à deux créneaux qui se recouvrent n'est présente qu'une fois :
    la partie commune revient au créneau commencé le premier, et donc à son lieu.
    """
    presences = defaultdict(dict)
    par_personne = defaultdict(list)
    for creneau in creneaux:
        par_personne[creneau.benevole].append(creneau)

    for personne, liste in par_personne.items():
        curseur = None   # fin du dernier créneau déjà compté
        for creneau in sorted(liste, key=lambda c: (c.debut, c.fin)):
            debut = creneau.debut if curseur is None else max(creneau.debut, curseur)
            if debut >= creneau.fin:
                continue  # entièrement recouvert par un créneau précédent
            curseur = creneau.fin if curseur is None else max(curseur, creneau.fin)

            for jour, plage, heures, pendant_repas in _portions(debut, creneau.fin, regles):
                presence = presences[jour].setdefault(personne, Presence(creneau.nom))
                par_lieu = presence.heures[plage.repas]
                par_lieu[creneau.lieu] = round(par_lieu.get(creneau.lieu, 0.0) + heures,
                                               PRECISION)
                if pendant_repas:
                    presence.fenetre[plage.repas] = round(
                        presence.fenetre.get(plage.repas, 0.0) + pendant_repas, PRECISION)
                presence.debut_lieu.setdefault(creneau.lieu, creneau.debut)

    return presences


# === Assemblage du tableau ===

def _cumul(*dictionnaires):
    total = defaultdict(float)
    for dictionnaire in dictionnaires:
        for cle, valeur in dictionnaire.items():
            total[cle] += valeur
    return total


def _lieu_dominant(par_lieu, debut_lieu, toutes_heures):
    """Le lieu où la personne passe le plus d'heures ; à égalité, le premier commencé.

    Plage vide (cas d'une fenêtre de repas active) : on retient le lieu dominant de la
    journée, pour que le repas ne disparaisse pas du décompte.
    """
    par_lieu = par_lieu or toutes_heures
    if not par_lieu:
        return SANS_LIEU
    return min(par_lieu, key=lambda lieu: (-par_lieu[lieu], debut_lieu.get(lieu)))


def lignes_du_jour(presences_du_jour, regles=REGLES_BENEVOLES, encadrements_du_jour=None,
                   regles_encadrants=REGLES_ENCADRANTS):
    """Une ligne par personne présente ce jour-là, bénévole ou encadrante, triée par nom."""
    encadrements_du_jour = encadrements_du_jour or {}
    lignes = []
    for personne in set(presences_du_jour) | set(encadrements_du_jour):
        benevolat = presences_du_jour.get(personne) or Presence('')
        encadrement = encadrements_du_jour.get(personne) or Presence('')

        debut_lieu = {}
        for source in (benevolat.debut_lieu, encadrement.debut_lieu):
            for lieu, debut in source.items():
                debut_lieu[lieu] = min(debut, debut_lieu.get(lieu, debut))
        toutes_heures = _cumul(*benevolat.heures.values(), *encadrement.heures.values())

        heures, encadre, lieux = {}, {}, {}
        for plage in regles.plages:
            par_lieu_benevolat = benevolat.heures.get(plage.repas, {})
            par_lieu_encadrement = encadrement.heures.get(plage.repas, {})
            heures[plage.repas] = regles.arrondir(sum(par_lieu_benevolat.values()))
            encadre[plage.repas] = sum(par_lieu_encadrement.values()) > 0
            lieux[plage.repas] = _lieu_dominant(
                _cumul(par_lieu_benevolat, par_lieu_encadrement), debut_lieu, toutes_heures)

        lignes.append(Ligne(personne, benevolat.nom or encadrement.nom, heures,
                            dict(benevolat.fenetre), lieux, encadre))
    return sorted(lignes, key=lambda ligne: (ligne.nom, ligne.benevole))


def tableau(creneaux, regles=REGLES_BENEVOLES, regles_encadrants=REGLES_ENCADRANTS):
    """{jour: [Ligne]} — le tableau complet, jours dans l'ordre.

    Les créneaux d'encadrement sans espace sont écartés : hors site, pas de repas.
    """
    if {p.repas for p in regles.plages} != {p.repas for p in regles_encadrants.plages}:
        raise ValueError('bénévoles et encadrants doivent avoir les mêmes repas')
    benevolat = heures_par_jour([c for c in creneaux if c.role != ENCADRANT], regles)
    encadrement = heures_par_jour(
        [c for c in creneaux if c.role == ENCADRANT and c.lieu != SANS_LIEU], regles_encadrants)
    return {jour: lignes_du_jour(benevolat.get(jour, {}), regles,
                                 encadrement.get(jour, {}), regles_encadrants)
            for jour in sorted(set(benevolat) | set(encadrement))}


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
