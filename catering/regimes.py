"""Régimes et exceptions alimentaires — données sensibles, à manier avec soin.

Deux sources dans NOÉ, que l'équipe tient à jour en validant chaque inscription :

- le **régime** : un champ texte du formulaire (« As-tu un régime alimentaire
  particulier ? »), que l'équipe réécrit en valeur générique (« végé », « vegan »…) ;
- les **exceptions** : des tags posés sur l'inscription (« Sans gluten », « Allergie à
  l'avoine »…), pour tout ce qui sort des régimes génériques.

Un régime peut révéler une donnée de santé (allergie, diabète) ou une conviction (halal,
casher) : article 9 du RGPD. D'où les précautions arrêtées avec l'équipe le 15/09/2026 :
mention de diffusion restreinte en tête du classeur, données confinées à une seule feuille,
comptages partout sauf pour les exceptions — la cuisine a besoin des noms en cas d'allergie
grave —, classeur supprimé à la fin du festival. Rien de nominatif en session.

Le classement des valeurs du champ vit ici, en données, comme les règles de repas : une
valeur non reconnue n'est pas devinée, elle ressort « à vérifier ».
"""

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass

from .regles import REGLES_BENEVOLES

# Phrase validée par l'équipe le 15/09/2026.
MENTION_RGPD = (
    'Ce fichier contient des régimes alimentaires, qui peuvent révéler des données de santé '
    'ou des convictions protégées par le RGPD (article 9). Diffusion réservée à l\'équipe '
    'catering. Ne pas imprimer, afficher ni transférer. Le fichier est protégé et sera '
    'supprimé à la fin du festival.'
)

STANDARD = 'Standard'
A_VERIFIER = 'À vérifier'
INCONNU = 'Inconnu'          # encadrant sans inscription reliée : pas de formulaire
FLEXI = 'Flexi-vegan'        # vegan de préférence, végétarien accepté — réparti au comptage


def normaliser(texte):
    """Minuscules, sans accents, espaces réduits : « Végé » → « vege »."""
    texte = unicodedata.normalize('NFKD', str(texte or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'\s+', ' ', texte).strip().lower()


@dataclass(frozen=True)
class Regimes:
    """Comment lire le champ régime et les tags d'une inscription."""

    # (catégorie, valeurs du champ qui y mènent), comparées une fois normalisées.
    categories: tuple = (
        ('Végétarien', ('végé', 'végétarien', 'végétarienne', 'veggie')),
        ('Végétalien', ('vegan', 'végane', 'végétalien', 'végétalienne')),
    )
    # Flexi-vegan (« vegan/végétarien », renseignement pris auprès de l'équipe le 15/09) :
    # vegan de préférence, végétarien accepté. Pas de plat à part : sur un même repas et un
    # même lieu, ils rejoignent les vegans s'il y en a déjà — le plat existe —, sinon les
    # végétariens.
    flexi: tuple = ('vegan/végétarien', 'flexi', 'flexi-vegan')
    flexi_prefere: str = 'Végétalien'
    flexi_repli: str = 'Végétarien'
    # Valeurs qui signifient « pas de régime particulier ».
    sans_regime: tuple = ('', 'non', 'aucun', 'aucune', 'rien', 'ras', 'no', '-', 'standard',
                          'normal')
    # Tags retenus comme exceptions. None : tous les tags de l'inscription — aujourd'hui
    # l'équipe n'en pose que pour cela. À restreindre si d'autres usages apparaissent.
    tags_exceptions: tuple = None

    def categorie(self, declaration):
        valeur = normaliser(declaration)
        if valeur in {normaliser(v) for v in self.sans_regime}:
            return STANDARD
        if valeur in {normaliser(v) for v in self.flexi}:
            return FLEXI
        for nom, valeurs in self.categories:
            if valeur in {normaliser(v) for v in valeurs}:
                return nom
        return A_VERIFIER

    def exceptions(self, tags):
        retenus = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
        if self.tags_exceptions is not None:
            permis = {normaliser(tag) for tag in self.tags_exceptions}
            retenus = [tag for tag in retenus if normaliser(tag) in permis]
        return tuple(retenus)

    def colonnes(self):
        """Les catégories dans l'ordre d'affichage."""
        return (STANDARD, *(nom for nom, _ in self.categories), A_VERIFIER, INCONNU)


REGIMES = Regimes()


def champ_regime(champs):
    """Clé du champ régime dans `NoeClient.form_fields()`, repérée par son intitulé — la
    clé elle-même porte un suffixe généré, impossible à deviner."""
    for cle, meta in champs.items():
        if 'regime' in normaliser(meta.get('label')):
            return cle
    return None


@dataclass(frozen=True)
class Profil:
    categorie: str
    exceptions: tuple
    declaration: str       # valeur du champ telle que saisie, utile pour les « à vérifier »


def profils_alimentaires(registrations, cle_champ, regimes=REGIMES):
    """{identifiant du compte NOÉ: Profil} — la même clé que celle des créneaux, ce qui
    couvre aussi les encadrants reliés à une inscription."""
    profils = {}
    for inscription in registrations:
        personne = inscription.get('user')
        if not isinstance(personne, dict) or not personne.get('_id'):
            continue
        reponses = inscription.get('formAnswers') or {}
        declaration = str(reponses.get(cle_champ) or '').strip() if cle_champ else ''
        profils[personne['_id']] = Profil(regimes.categorie(declaration),
                                          regimes.exceptions(inscription.get('tags')),
                                          declaration)
    return profils


def tags_utilises(registrations):
    """Counter des tags posés sur les inscriptions — des noms de tags, aucune personne.
    Sert à repérer un tag qui ne serait pas alimentaire."""
    return Counter(str(tag).strip() for inscription in registrations
                   for tag in (inscription.get('tags') or []) if str(tag).strip())


@dataclass(frozen=True)
class Repartition:
    """Les repas servis pour un jour, un repas et un lieu, par catégorie de régime."""

    jour: object
    plage: object
    lieu: str
    categories: Counter    # flexi-vegans déjà répartis
    avec_exception: int
    dont_flexi: int = 0


@dataclass(frozen=True)
class Signalement:
    """Une personne à signaler en cuisine pour un repas qui lui est accordé."""

    jour: object
    plage: object
    lieu: str
    nom: str
    exceptions: tuple
    declaration: str       # rempli seulement pour un régime à vérifier


def _profil(profils, ligne):
    return profils.get(ligne.benevole) or Profil(INCONNU, (), '')


def repartition(jours, profils, regles=REGLES_BENEVOLES, regimes=REGIMES):
    """[Repartition] — seuls les repas accordés comptent, dans l'ordre jour, repas, lieu."""
    resultat = []
    for jour, lignes in jours.items():
        for plage in regles.plages:
            categories, exceptions = defaultdict(Counter), Counter()
            for ligne in lignes:
                if not ligne.a_droit(plage):
                    continue
                lieu, profil = ligne.lieux[plage.repas], _profil(profils, ligne)
                categories[lieu][profil.categorie] += 1
                exceptions[lieu] += bool(profil.exceptions)
            for lieu in sorted(categories):
                compte = categories[lieu]
                flexis = compte.pop(FLEXI, 0)
                if flexis:
                    cible = (regimes.flexi_prefere if compte[regimes.flexi_prefere] > 0
                             else regimes.flexi_repli)
                    compte[cible] += flexis
                resultat.append(Repartition(jour, plage, lieu, compte, exceptions[lieu], flexis))
    return resultat


def signalements(jours, profils, regles=REGLES_BENEVOLES):
    """[Signalement] — exceptions alimentaires et régimes à vérifier, par repas accordé."""
    resultat = []
    for jour, lignes in jours.items():
        for plage in regles.plages:
            du_repas = []
            for ligne in lignes:
                profil = _profil(profils, ligne)
                a_verifier = profil.categorie == A_VERIFIER
                if ligne.a_droit(plage) and (profil.exceptions or a_verifier):
                    du_repas.append(Signalement(jour, plage, ligne.lieux[plage.repas], ligne.nom,
                                                profil.exceptions,
                                                profil.declaration if a_verifier else ''))
            resultat += sorted(du_repas, key=lambda s: (s.lieu, s.nom))
    return resultat
