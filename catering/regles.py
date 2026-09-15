"""Les règles du catering, regroupées en données.

Les règles viennent de l'équipe du festival et bougent au fil des discussions : plages
horaires, seuils et arrondi ont déjà changé deux fois avant l'événement. Elles vivent donc
ici, et `calcul.py` ne fait que les appliquer — changer une règle revient à changer une
valeur, pas du code.

Une instance de `Regles` par population : les bénévoles aujourd'hui, les encadrants
ensuite, avec leurs propres plages (celles des encadrants se chevauchent, volontairement).

Règles des bénévoles, arrêtées avec l'équipe le 15/09/2026 :

    matin  08:00 – 16:00   ≥ 2 h de présence  →  déjeuner
    soir   16:00 – 03:00   ≥ 2 h de présence  →  dîner    (heures après minuit : la veille)

    heures de chaque plage arrondies au quart d'heure : un reste de 15 min ou moins part
    vers le bas, au-delà vers l'heure pleine suivante (1 h 16 compte 2 h)
"""

from dataclasses import dataclass, replace

# Les heures sont arrondies au dix-millième (0,36 s) avant toute comparaison : sans cela,
# une somme de flottants peut rendre 1,999999 pour deux heures pleines et faire sauter un
# repas. Même arrondi que celui écrit dans le tableur.
PRECISION = 4


def _hhmm(heures):
    """17.5 → « 17:30 » ; au-delà de 24, l'heure du lendemain (27 → « 03:00 »)."""
    minutes = round(heures * 60) % (24 * 60)
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


@dataclass(frozen=True)
class Plage:
    """Une partie de la journée qui ouvre droit à un repas.

    Les heures se comptent depuis minuit du jour J et peuvent dépasser 24 : `fin=27`
    signifie 03:00 le lendemain, et ces heures de nuit restent rattachées au jour J.
    """

    repas: str                  # clé technique, ASCII : 'dejeuner', 'diner'
    nom_repas: str              # « Déjeuner », « Dîner » — pour le tableau
    libelle: str                # « matin », « soir »
    debut: float
    fin: float
    seuil: float = 2.0          # heures de présence (après arrondi) ouvrant le repas
    # Heures du repas lui-même. Quand `presence_fenetre` vaut une durée, être présent au
    # moins cette durée pendant la fenêtre ouvre aussi le repas ; None ou 0 la désactive.
    # Écartée par l'équipe le 15/09, gardée en paramètre au cas où elle y reviendrait.
    fenetre: tuple = None
    presence_fenetre: float = None

    def __post_init__(self):
        if not (self.repas.isascii() and self.repas.isidentifier()):
            raise ValueError(f'clé de repas invalide : {self.repas!r}')
        if not 0 <= self.debut < self.fin <= self.debut + 24:
            raise ValueError(f'plage {self.libelle!r} : bornes incohérentes '
                             f'({self.debut} → {self.fin})')
        if self.fenetre and not self.debut <= self.fenetre[0] < self.fenetre[1] <= self.fin:
            raise ValueError(f'plage {self.libelle!r} : la fenêtre du repas '
                             f'{self.fenetre} sort de la plage')

    @property
    def fenetre_active(self):
        return bool(self.fenetre and self.presence_fenetre)

    def description(self):
        texte = (f'{self.nom_repas.lower()} : ≥ {self.seuil:g} h entre {_hhmm(self.debut)} '
                 f'et {_hhmm(self.fin)}')
        if self.fenetre_active:
            texte += (f', ou ≥ {self.presence_fenetre:g} h entre {_hhmm(self.fenetre[0])} '
                      f'et {_hhmm(self.fenetre[1])}')
        return texte


@dataclass(frozen=True)
class Regles:
    """Les plages d'une population, et l'arrondi de ses heures."""

    plages: tuple
    # Tolérance de l'arrondi, en minutes : un reste inférieur ou égal part vers le bas, au-delà
    # vers l'heure pleine suivante. None : heures exactes, sans arrondi.
    tolerance_arrondi: int = 15

    def __post_init__(self):
        cles = [plage.repas for plage in self.plages]
        if len(cles) != len(set(cles)):
            raise ValueError(f'un même repas ne peut ouvrir qu\'une plage : {cles}')

    def arrondir(self, heures):
        if self.tolerance_arrondi is None:
            return round(heures, PRECISION)
        pleines, reste = divmod(round(heures * 60), 60)
        return float(pleines + (1 if reste > self.tolerance_arrondi else 0))

    def avec_seuils(self, **seuils):
        """Les mêmes règles, avec d'autres seuils : `avec_seuils(dejeuner=3)`."""
        inconnus = set(seuils) - {plage.repas for plage in self.plages}
        if inconnus:
            raise ValueError(f'repas inconnu(s) : {", ".join(sorted(inconnus))}')
        return replace(self, plages=tuple(
            replace(plage, seuil=seuils.get(plage.repas, plage.seuil)) for plage in self.plages))

    def description(self):
        texte = ' ; '.join(plage.description() for plage in self.plages)
        if self.tolerance_arrondi is not None:
            texte += (f' ; heures arrondies à l\'heure (reste ≤ {self.tolerance_arrondi} min '
                      'vers le bas, au-delà vers le haut)')
        return texte


REGLES_BENEVOLES = Regles(plages=(
    Plage('dejeuner', 'Déjeuner', 'matin', debut=8, fin=16, seuil=2, fenetre=(12, 14)),
    Plage('diner', 'Dîner', 'soir', debut=16, fin=27, seuil=2, fenetre=(19, 21.5)),
))
