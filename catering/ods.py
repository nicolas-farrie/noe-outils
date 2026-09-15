"""Écriture du classeur `.ods` — formules vivantes, sans dépendance.

Un `.ods` est une archive zip de quelques fichiers XML ; la bibliothèque standard suffit
à l'écrire. Le faire à la main plutôt qu'avec `odfpy` a une raison précise : le classeur
porte des **plages nommées** (`seuilDejeuner`, `seuilDiner`) et des formules `SUMIFS`.
L'équipe change un seuil dans la feuille « Variables » et tout le classeur se recalcule.

Ce que le tableur ne peut pas refaire reste calculé par Python et écrit en valeur : le
découpage des créneaux sur les plages, l'arrondi, le choix du lieu, et la fenêtre de
repas si elle est activée.

Chaque cellule calculée porte **à la fois sa formule et sa valeur** : un aperçu qui ne
recalcule pas affiche quand même les bons chiffres.

Disposition : « Récap », « Régimes », une feuille par journée, « Anomalies » s'il y en a,
« Variables ».
Le bloc « repas par lieu » liste les mêmes lieux, dans le même ordre, sur toutes les
feuilles de journée — c'est ce qui permet au récapitulatif de les pointer par adresse.
"""

import zipfile
from datetime import datetime
from xml.sax.saxutils import escape, quoteattr

from .calcul import SANS_LIEU, totaux_par_lieu
from .regimes import MENTION_RGPD, REGIMES, repartition, signalements
from .regles import REGLES_BENEVOLES, REGLES_ENCADRANTS, _hhmm

MIMETYPE = 'application/vnd.oasis.opendocument.spreadsheet'

_ESPACES_DE_NOMS = ' '.join(f'xmlns:{prefixe}={quoteattr(uri)}' for prefixe, uri in (
    ('office', 'urn:oasis:names:tc:opendocument:xmlns:office:1.0'),
    ('table', 'urn:oasis:names:tc:opendocument:xmlns:table:1.0'),
    ('text', 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'),
    ('style', 'urn:oasis:names:tc:opendocument:xmlns:style:1.0'),
    ('fo', 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0'),
    ('number', 'urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0'),
    ('of', 'urn:oasis:names:tc:opendocument:xmlns:of:1.2'),
    ('meta', 'urn:oasis:names:tc:opendocument:xmlns:meta:1.0'),
    ('dc', 'http://purl.org/dc/elements/1.1/'),
))

_STYLES = (
    '<style:style style:name="col-large" style:family="table-column">'
    '<style:table-column-properties style:column-width="6cm"/></style:style>'
    '<style:style style:name="col-moyenne" style:family="table-column">'
    '<style:table-column-properties style:column-width="3.2cm"/></style:style>'
    '<style:style style:name="gras" style:family="table-cell">'
    '<style:text-properties fo:font-weight="bold"/></style:style>'
    '<style:style style:name="titre" style:family="table-cell">'
    '<style:text-properties fo:font-weight="bold" fo:font-size="14pt"/></style:style>'
    # Colonnes de résultat centrées, en-têtes compris : les chiffres s'alignent sous leur
    # titre et se lisent d'un coup d'œil. Noms et lieux restent à gauche.
    '<style:style style:name="centre" style:family="table-cell">'
    '<style:paragraph-properties fo:text-align="center"/></style:style>'
    '<style:style style:name="centre-gras" style:family="table-cell">'
    '<style:paragraph-properties fo:text-align="center"/>'
    '<style:text-properties fo:font-weight="bold"/></style:style>'
)


# === Briques XML ===

def _vide(nombre=1):
    if nombre == 1:
        return '<table:table-cell/>'
    return f'<table:table-cell table:number-columns-repeated="{nombre}"/>'


def _style(nom):
    return f' table:style-name={quoteattr(nom)}' if nom else ''


def _texte(valeur, style=None):
    return (f'<table:table-cell{_style(style)} office:value-type="string">'
            f'<text:p>{escape(str(valeur))}</text:p></table:table-cell>')


def _nombre(valeur, style=None):
    return (f'<table:table-cell{_style(style)} office:value-type="float" '
            f'office:value="{valeur:g}"><text:p>{valeur:g}</text:p></table:table-cell>')


def _formule(formule, valeur, style=None):
    """Une cellule calculée : la formule pour le tableur, la valeur pour les aperçus."""
    return (f'<table:table-cell{_style(style)} table:formula={quoteattr("of:=" + formule)} '
            f'office:value-type="float" office:value="{valeur:g}">'
            f'<text:p>{valeur:g}</text:p></table:table-cell>')


def _rangee(*cellules):
    return '<table:table-row>' + ''.join(cellules) + '</table:table-row>'


def _feuille(nom, colonnes, rangees):
    largeurs = ''.join(
        f'<table:table-column table:style-name={quoteattr(style)}/>' for style in colonnes)
    return (f'<table:table table:name={quoteattr(nom)}>' + largeurs
            + ''.join(rangees) + '</table:table>')


def _colonne(indice):
    """La lettre d'une colonne : 0 → A, 25 → Z, 26 → AA."""
    lettres = ''
    indice += 1
    while indice:
        indice, reste = divmod(indice - 1, 26)
        lettres = chr(ord('A') + reste) + lettres
    return lettres


# === Feuilles ===

def _nom_feuille(jour):
    return f'{jour:%Y-%m-%d}'


def _nom_seuil(plage):
    """Nom de la plage nommée qui porte le seuil : `seuilDejeuner`, `seuilDiner`."""
    return 'seuil' + plage.repas[0].upper() + plage.repas[1:]


def _abreviation(plage):
    return plage.nom_repas[:3].lower() + '.'


def _reference(jour, cellule):
    """Référence à une cellule d'une feuille de journée, depuis une autre feuille.

    Le nom d'une feuille de journée est une date, donc il commence par un chiffre : il
    doit être protégé par des apostrophes — `[$'2026-09-26'.B12]`.
    """
    return "[$'" + _nom_feuille(jour) + "'." + cellule + ']'


def _lieux_du_classeur(jours):
    """Tous les lieux où un repas peut être servi, dans un ordre stable — « (sans lieu) »
    à la fin."""
    lieux = set()
    for lignes in jours.values():
        for ligne in lignes:
            lieux.update(ligne.lieux.values())
    ordinaires = sorted(lieu for lieu in lieux if lieu != SANS_LIEU)
    return ordinaires + ([SANS_LIEU] if SANS_LIEU in lieux else [])


def _feuille_journee(jour, lignes, lieux, regles):
    """Une journée : le détail par personne, puis le récapitulatif par lieu.

    Colonnes : A nom ; puis, par plage, lieu, heures de bénévolat et encadrement (1 ou 0) ;
    puis, par plage, le repas. Renvoie (xml, rangee_premier_lieu) — le récapitulatif du
    classeur a besoin de savoir à quelle rangée commence le bloc des lieux.
    """
    plages = regles.plages
    nombre = len(plages)
    col_lieu = {p.repas: _colonne(1 + 3 * i) for i, p in enumerate(plages)}
    col_heures = {p.repas: _colonne(2 + 3 * i) for i, p in enumerate(plages)}
    col_encadre = {p.repas: _colonne(3 + 3 * i) for i, p in enumerate(plages)}
    col_repas = {p.repas: _colonne(1 + 3 * nombre + i) for i, p in enumerate(plages)}

    entetes = [_texte('Nom', 'gras')]
    for plage in plages:
        entetes += [_texte(f'Lieu du {plage.libelle}', 'gras'),
                    _texte(f'Heures {plage.libelle}', 'centre-gras'),
                    _texte(f'Encadre {plage.libelle}', 'centre-gras')]
    entetes += [_texte(plage.nom_repas, 'centre-gras') for plage in plages]
    rangees = [
        _rangee(_texte(f'JOURNÉE DU {jour:%d/%m/%Y}', 'titre')),
        _rangee(),
        _rangee(*entetes),
    ]

    premiere = len(rangees) + 1                     # première rangée de données (1-indexée)
    for decalage, ligne in enumerate(lignes):
        numero = premiere + decalage
        cellules = [_texte(ligne.nom)]
        for plage in plages:
            cellules += [_texte(ligne.lieux[plage.repas]),
                         _nombre(ligne.heures[plage.repas], 'centre'),
                         _nombre(1 if ligne.encadre.get(plage.repas) else 0, 'centre')]
        for plage in plages:
            valeur = 1 if ligne.a_droit(plage) else 0
            if plage.fenetre_active:
                # La présence pendant le repas dépend du détail des créneaux, que le tableur
                # n'a pas : la cellule porte la valeur calculée, sans formule.
                cellules.append(_nombre(valeur, 'centre'))
            else:
                heures = f'[.{col_heures[plage.repas]}{numero}]'
                encadre = f'[.{col_encadre[plage.repas]}{numero}]'
                # Bénévole au-dessus du seuil — et présent : un seuil ramené à zéro ne doit
                # pas nourrir les absents —, ou encadrant d'une session de la plage.
                cellules.append(_formule(
                    f'IF(OR(AND({heures}>0;{heures}>={_nom_seuil(plage)});{encadre}=1);1;0)',
                    valeur, 'centre'))
        rangees.append(_rangee(*cellules))
    derniere = premiere + len(lignes) - 1

    par_lieu = totaux_par_lieu(lignes, regles)
    totaux = {plage.repas: sum(compte[plage.repas] for compte in par_lieu.values())
              for plage in plages}

    rangees.append(_rangee(
        _texte('Total de la journée', 'gras'), _vide(3 * nombre),
        *(_formule(f'SUM([.{col_repas[p.repas]}{premiere}:.{col_repas[p.repas]}{derniere}])',
                   totaux[p.repas], 'centre-gras') for p in plages)))
    rangees.append(_rangee())
    rangees.append(_rangee(_texte('Repas par lieu', 'gras')))
    rangees.append(_rangee(_texte('Lieu', 'gras'),
                           *(_texte(f'{p.nom_repas}s', 'centre-gras') for p in plages)))

    def somme_du_lieu(plage, numero):
        # Le lieu d'un repas est celui de sa plage : le déjeuner se compte sur la colonne
        # « lieu du matin », le dîner sur « lieu du soir ».
        repas, lieu = col_repas[plage.repas], col_lieu[plage.repas]
        return (f'SUMIFS([.{repas}${premiere}:.{repas}${derniere}];'
                f'[.{lieu}${premiere}:.{lieu}${derniere}];[.A{numero}])')

    premier_lieu = len(rangees) + 1
    for decalage, lieu in enumerate(lieux):
        numero = premier_lieu + decalage
        attendus = par_lieu.get(lieu, {})
        rangees.append(_rangee(_texte(lieu), *(
            _formule(somme_du_lieu(p, numero), attendus.get(p.repas, 0), 'centre')
            for p in plages)))
    dernier_lieu = premier_lieu + len(lieux) - 1

    rangees.append(_rangee(_texte('Total', 'gras'), *(
        _formule(f'SUM([.{_colonne(1 + i)}{premier_lieu}:.{_colonne(1 + i)}{dernier_lieu}])',
                 totaux[p.repas], 'centre-gras') for i, p in enumerate(plages))))

    colonnes = ['col-large'] + ['col-moyenne'] * (4 * nombre)
    return _feuille(_nom_feuille(jour), colonnes, rangees), premier_lieu


def _feuille_recap(jours, lieux, premieres_rangees, regles, regles_encadrants, genere_le,
                   mention=None):
    """Un tableau lieux × journées, pointant les totaux de chaque feuille de journée."""
    plages = regles.plages
    entetes = [_texte('Lieu', 'gras')]
    for jour in jours:
        entetes += [_texte(f'{jour:%d/%m} {_abreviation(p)}', 'centre-gras') for p in plages]

    rangees = [
        _rangee(_texte('Repas à prévoir', 'titre')),
        *([_rangee(_texte(mention, 'gras'))] if mention else []),
        _rangee(_texte(f'Mis à jour le {genere_le:%d/%m/%Y à %H:%M}')),
        _rangee(_texte(f'Bénévoles — {regles.description()}')),
        _rangee(_texte(f'Encadrants sur un espace — {regles_encadrants.description()}')),
        _rangee(),
        _rangee(*entetes),
    ]

    premiere = len(rangees) + 1
    totaux = {jour: totaux_par_lieu(lignes, regles) for jour, lignes in jours.items()}
    for decalage, lieu in enumerate(lieux):
        cellules = [_texte(lieu)]
        for jour in jours:
            rangee_lieu = premieres_rangees[jour] + decalage
            attendus = totaux[jour].get(lieu, {})
            cellules += [_formule(_reference(jour, f'{_colonne(1 + i)}{rangee_lieu}'),
                                  attendus.get(p.repas, 0), 'centre')
                         for i, p in enumerate(plages)]
        rangees.append(_rangee(*cellules))
    derniere = premiere + len(lieux) - 1

    cellules = [_texte('Total', 'gras')]
    for indice_jour, jour in enumerate(jours):
        for i, plage in enumerate(plages):
            colonne = _colonne(1 + indice_jour * len(plages) + i)
            attendu = sum(compte[plage.repas] for compte in totaux[jour].values())
            cellules.append(_formule(f'SUM([.{colonne}{premiere}:.{colonne}{derniere}])',
                                     attendu, 'centre-gras'))
    rangees.append(_rangee(*cellules))

    colonnes = ['col-large'] + ['col-moyenne'] * (len(plages) * len(jours))
    return _feuille('Récap', colonnes, rangees)


def _feuille_anomalies(anomalies):
    """Les créneaux qui débordent des plages : à corriger ou à confirmer dans NOÉ."""
    rangees = [
        _rangee(_texte('Anomalies — heures hors des plages horaires', 'titre')),
        _rangee(_texte('Ces heures ne tombent dans aucune plage et ne comptent pour aucun '
                       'repas : erreur de saisie ou heures de nuit, à vérifier dans NOÉ.')),
        _rangee(),
        _rangee(_texte('Nom', 'gras'), _texte('Rôle', 'gras'), _texte('Lieu', 'gras'),
                _texte('Début', 'centre-gras'), _texte('Fin', 'centre-gras'),
                _texte('Heures hors plages', 'centre-gras')),
    ]
    for anomalie in anomalies:
        creneau = anomalie.creneau
        rangees.append(_rangee(
            _texte(creneau.nom), _texte(creneau.role), _texte(creneau.lieu),
            _texte(f'{creneau.debut:%d/%m %H:%M}', 'centre'),
            _texte(f'{creneau.fin:%d/%m %H:%M}', 'centre'),
            _nombre(anomalie.heures, 'centre')))
    return _feuille('Anomalies', ['col-large', 'col-moyenne', 'col-large'] + ['col-moyenne'] * 3,
                    rangees)


def _feuille_regimes(jours, profils, regles, regimes):
    """Régimes et exceptions : comptages par repas servi, puis la liste pour la cuisine.

    Données sensibles (article 9 du RGPD) : elles restent confinées à cette feuille, sous
    la mention de diffusion restreinte. Les comptages sont écrits en valeurs, avec les
    seuils en vigueur à la génération — le tableur n'a pas le régime de chaque personne.
    """
    categories = regimes.colonnes()
    rangees = [
        _rangee(_texte('Régimes et exceptions alimentaires', 'titre')),
        _rangee(_texte(MENTION_RGPD, 'gras')),
        _rangee(_texte('Comptés sur les repas accordés, avec les seuils en vigueur à la '
                       'génération : modifier un seuil dans « Variables » ne recalcule pas '
                       'cette feuille.')),
        _rangee(_texte('Régime : champ du formulaire d\'inscription. Exceptions : tags posés '
                       'par l\'équipe dans NOÉ. « Inconnu » : encadrant sans inscription '
                       'reliée.')),
        _rangee(_texte(f'Flexi-vegan : comptés avec les {regimes.flexi_prefere.lower()}s '
                       'quand il y en a sur le même repas et le même lieu, sinon avec les '
                       f'{regimes.flexi_repli.lower()}s (colonne « Dont flexi »).')),
        _rangee(),
        _rangee(_texte('Jour', 'gras'), _texte('Repas', 'gras'), _texte('Lieu', 'gras'),
                _texte('Repas servis', 'centre-gras'),
                *(_texte(categorie, 'centre-gras') for categorie in categories),
                _texte('Dont flexi', 'centre-gras'),
                _texte('Avec exception', 'centre-gras')),
    ]
    for ligne in repartition(jours, profils, regles, regimes):
        rangees.append(_rangee(
            _texte(f'{ligne.jour:%d/%m}'), _texte(ligne.plage.nom_repas), _texte(ligne.lieu),
            _nombre(sum(ligne.categories.values()), 'centre'),
            *(_nombre(ligne.categories.get(categorie, 0), 'centre') for categorie in categories),
            _nombre(ligne.dont_flexi, 'centre'),
            _nombre(ligne.avec_exception, 'centre')))

    rangees += [
        _rangee(),
        _rangee(_texte('À signaler en cuisine — exceptions alimentaires et régimes à vérifier',
                       'gras')),
    ]
    a_signaler = signalements(jours, profils, regles)
    if a_signaler:
        rangees.append(_rangee(*(_texte(entete, 'gras') for entete in (
            'Jour', 'Repas', 'Lieu', 'Nom', 'Exceptions', 'Régime déclaré'))))
        for signalement in a_signaler:
            rangees.append(_rangee(
                _texte(f'{signalement.jour:%d/%m}'), _texte(signalement.plage.nom_repas),
                _texte(signalement.lieu), _texte(signalement.nom),
                _texte(', '.join(signalement.exceptions)), _texte(signalement.declaration)))
    else:
        rangees.append(_rangee(_texte('Personne à signaler.')))

    colonnes = ['col-moyenne', 'col-moyenne'] + ['col-large'] * 4 + ['col-moyenne'] * len(categories)
    return _feuille('Régimes', colonnes, rangees)


def _feuille_variables(regles, regles_encadrants):
    """Les seuils, nommés — c'est ici que l'équipe les ajuste — puis les règles fixes."""
    rangees = [_rangee(_texte('Variables', 'titre'))]
    for plage in regles.plages:
        rangees.append(_rangee(
            _texte(_nom_seuil(plage)), _nombre(plage.seuil, 'centre'),
            _texte(f'heures de présence du {plage.libelle} ({_hhmm(plage.debut)}–'
                   f'{_hhmm(plage.fin)}) ouvrant droit au {plage.nom_repas.lower()}')))
    rangees += [
        _rangee(),
        _rangee(_texte('Modifier un seuil recalcule tout le classeur.')),
        _rangee(_texte('Le fichier est régénéré automatiquement : une modification '
                       'sera écrasée à la prochaine mise à jour.')),
        _rangee(),
        _rangee(_texte('Règles appliquées au calcul (non modifiables ici)', 'gras')),
    ]
    if regles.tolerance_arrondi is not None:
        rangees.append(_rangee(_texte(
            f'Heures de chaque plage arrondies à l\'heure : un reste de '
            f'{regles.tolerance_arrondi} min ou moins part vers le bas, au-delà vers le haut.')))
    rangees += [
        _rangee(_texte(f'Encadrants affectés à une session sur un espace — '
                       f'{regles_encadrants.description()}, quelle que soit la durée.')),
        _rangee(_texte('Une personne n\'a qu\'un repas par plage, servi au lieu où elle passe '
                       'le plus d\'heures, même bénévole et encadrante à la fois.')),
    ]
    for plage in regles.plages:
        if plage.fenetre:
            etat = (f'active, ≥ {plage.presence_fenetre:g} h de présence'
                    if plage.fenetre_active else 'désactivée')
            rangees.append(_rangee(_texte(
                f'Fenêtre du {plage.nom_repas.lower()} ({_hhmm(plage.fenetre[0])}–'
                f'{_hhmm(plage.fenetre[1])}) : {etat}.')))
    return _feuille('Variables', ['col-large', 'col-moyenne', 'col-large'], rangees)


def _plages_nommees(regles):
    """Les seuils nommés, utilisés par les formules des feuilles de journée."""
    noms = ''.join(
        f'<table:named-range table:name="{_nom_seuil(plage)}" '
        f'table:base-cell-address="$Variables.$B${2 + i}" '
        f'table:cell-range-address="$Variables.$B${2 + i}"/>'
        for i, plage in enumerate(regles.plages))
    return f'<table:named-expressions>{noms}</table:named-expressions>'


# === Assemblage ===

def _contenu(jours, regles, regles_encadrants, anomalies, genere_le, profils, regimes):
    lieux = _lieux_du_classeur(jours)
    feuilles = []
    premieres_rangees = {}
    for jour, lignes in jours.items():
        xml, premiere = _feuille_journee(jour, lignes, lieux, regles)
        feuilles.append(xml)
        premieres_rangees[jour] = premiere

    # La mention RGPD n'a de sens que si le classeur porte les régimes.
    avec_regimes = profils is not None
    corps = (_feuille_recap(jours, lieux, premieres_rangees, regles, regles_encadrants,
                            genere_le, MENTION_RGPD if avec_regimes else None)
             # Juste après le récapitulatif : c'est la feuille que la cuisine cherche, elle
             # ne doit pas se perdre derrière les journées.
             + (_feuille_regimes(jours, profils, regles, regimes) if avec_regimes else '')
             + ''.join(feuilles)
             + (_feuille_anomalies(anomalies) if anomalies else '')
             + _feuille_variables(regles, regles_encadrants) + _plages_nommees(regles))

    return ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<office:document-content {_ESPACES_DE_NOMS} office:version="1.3">'
            f'<office:automatic-styles>{_STYLES}</office:automatic-styles>'
            f'<office:body><office:spreadsheet>{corps}'
            '</office:spreadsheet></office:body></office:document-content>')


def _styles():
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<office:document-styles {_ESPACES_DE_NOMS} office:version="1.3">'
            '<office:styles/><office:automatic-styles/><office:master-styles/>'
            '</office:document-styles>')


def _meta(genere_le):
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<office:document-meta {_ESPACES_DE_NOMS} office:version="1.3">'
            '<office:meta><meta:generator>noe-outils/catering</meta:generator>'
            '<dc:title>Repas à prévoir</dc:title>'
            f'<dc:date>{genere_le:%Y-%m-%dT%H:%M:%S}</dc:date>'
            '</office:meta></office:document-meta>')


_MANIFESTE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<manifest:manifest '
    'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
    'manifest:version="1.3">'
    f'<manifest:file-entry manifest:full-path="/" manifest:version="1.3" '
    f'manifest:media-type="{MIMETYPE}"/>'
    '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
    '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
    '<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>'
    '</manifest:manifest>'
)


def ecrire_classeur(chemin, jours, regles=REGLES_BENEVOLES, anomalies=(), genere_le=None,
                    regles_encadrants=REGLES_ENCADRANTS, profils=None, regimes=REGIMES):
    """Écrit le classeur des repas.

    Args:
        chemin: fichier `.ods` à écrire (écrasé s'il existe)
        jours: {date: [Ligne]} — cf. `calcul.tableau()`
        regles: les règles appliquées, dont les seuils recopiés dans « Variables »
        anomalies: `calcul.releve_anomalies()` — une feuille dédiée s'il y en a
        genere_le: horodatage affiché dans le récapitulatif (maintenant par défaut)
        regles_encadrants: règles des encadrants, rappelées dans le classeur
        profils: `regimes.profils_alimentaires()` — ajoute la feuille « Régimes » et la
                 mention RGPD ; None, classeur sans régimes
        regimes: le classement des régimes, pour l'ordre des colonnes
    """
    if not jours:
        raise ValueError('aucune journée à écrire')
    genere_le = genere_le or datetime.now()

    with zipfile.ZipFile(chemin, 'w', zipfile.ZIP_DEFLATED) as archive:
        # Le mimetype doit être la première entrée et rester non compressé : c'est à cela
        # qu'un lecteur reconnaît un document OpenDocument sans le décompresser.
        archive.writestr(zipfile.ZipInfo('mimetype'), MIMETYPE,
                         compress_type=zipfile.ZIP_STORED)
        archive.writestr('META-INF/manifest.xml', _MANIFESTE)
        archive.writestr('content.xml',
                         _contenu(jours, regles, regles_encadrants, anomalies, genere_le,
                                  profils, regimes))
        archive.writestr('styles.xml', _styles())
        archive.writestr('meta.xml', _meta(genere_le))
    return chemin
