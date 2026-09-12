"""Écriture du classeur `.ods` — formules vivantes, sans dépendance.

Un `.ods` est une archive zip de quelques fichiers XML ; la bibliothèque standard suffit
à l'écrire. Le faire à la main plutôt qu'avec `odfpy` a une raison précise : le classeur
doit porter des **plages nommées** (`nbHeureAM`, `nbHeurePM`, `nbHeureJour`) et des
formules `SUMIFS`, comme le fichier d'exemple de l'équipe. L'équipe change une valeur dans
la feuille « Variables » et tout le classeur se recalcule.

Chaque cellule calculée porte **à la fois sa formule et sa valeur** : un aperçu qui ne
recalcule pas (l'application Seafile sur téléphone, par exemple) affiche quand même les
bons chiffres.

Disposition : une feuille « Récap », une feuille par journée, une feuille « Variables ».
Le bloc « repas par lieu » liste les mêmes lieux, dans le même ordre, sur toutes les
feuilles de journée — c'est ce qui permet au récapitulatif de les pointer par leur adresse.
"""

import zipfile
from datetime import datetime
from xml.sax.saxutils import escape, quoteattr

from .calcul import SANS_LIEU, SEUILS_DEFAUT, totaux_par_lieu

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


# === Feuilles ===

def _nom_feuille(jour):
    return f'{jour:%Y-%m-%d}'


def _reference(jour, cellule):
    """Référence à une cellule d'une feuille de journée, depuis une autre feuille.

    Le nom d'une feuille de journée est une date, donc il commence par un chiffre : il
    doit être protégé par des apostrophes — `[$'2026-09-26'.B12]`.
    """
    return "[$'" + _nom_feuille(jour) + "'." + cellule + ']'


def _lieux_du_classeur(jours):
    """Tous les lieux rencontrés, dans un ordre stable — « (sans lieu) » à la fin."""
    lieux = set()
    for lignes in jours.values():
        for ligne in lignes:
            lieux.update((ligne.lieu_matin, ligne.lieu_soir))
    ordinaires = sorted(lieu for lieu in lieux if lieu != SANS_LIEU)
    return ordinaires + ([SANS_LIEU] if SANS_LIEU in lieux else [])


def _feuille_journee(jour, lignes, lieux, seuils):
    """Une journée : le détail par bénévole, puis le récapitulatif par lieu.

    Renvoie (xml, rangee_premier_lieu) — le récapitulatif du classeur a besoin de savoir
    à quelle rangée commence le bloc des lieux.
    """
    rangees = [
        _rangee(_texte(f'JOURNÉE DU {jour:%d/%m/%Y}', 'titre')),
        _rangee(),
        _rangee(*(_texte(entete, 'gras') for entete in (
            'Bénévole', 'Lieu du matin', 'Heures AM', 'Lieu du soir', 'Heures PM',
            'Heures JOUR', 'Déjeuner', 'Dîner'))),
    ]

    premiere = len(rangees) + 1                     # première rangée de données (1-indexée)
    for decalage, ligne in enumerate(lignes):
        numero = premiere + decalage
        rangees.append(_rangee(
            _texte(ligne.nom),
            _texte(ligne.lieu_matin), _nombre(ligne.am),
            _texte(ligne.lieu_soir), _nombre(ligne.pm),
            _formule(f'[.C{numero}]+[.E{numero}]', ligne.jour),
            _formule(f'IF(OR([.C{numero}]>=nbHeureAM;[.F{numero}]>=nbHeureJour);1;0)',
                     1 if ligne.dejeuner(seuils) else 0),
            _formule(f'IF(OR([.E{numero}]>=nbHeurePM;[.F{numero}]>=nbHeureJour);1;0)',
                     1 if ligne.diner(seuils) else 0),
        ))
    derniere = premiere + len(lignes) - 1

    par_lieu = totaux_par_lieu(lignes, seuils)
    dejeuners = sum(compte[0] for compte in par_lieu.values())
    diners = sum(compte[1] for compte in par_lieu.values())

    rangees.append(_rangee(
        _texte('Total de la journée', 'gras'), _vide(5),
        _formule(f'SUM([.G{premiere}:.G{derniere}])', dejeuners, 'gras'),
        _formule(f'SUM([.H{premiere}:.H{derniere}])', diners, 'gras'),
    ))
    rangees.append(_rangee())
    rangees.append(_rangee(_texte('Repas par lieu', 'gras')))
    rangees.append(_rangee(*(_texte(entete, 'gras')
                             for entete in ('Lieu', 'Déjeuners', 'Dîners'))))

    premier_lieu = len(rangees) + 1
    for decalage, lieu in enumerate(lieux):
        numero = premier_lieu + decalage
        attendus = par_lieu.get(lieu, (0, 0))
        rangees.append(_rangee(
            _texte(lieu),
            # Le lieu du repas est celui de la demi-journée concernée : le déjeuner se
            # compte sur la colonne « lieu du matin », le dîner sur « lieu du soir ».
            _formule(f'SUMIFS([.G${premiere}:.G${derniere}];'
                     f'[.B${premiere}:.B${derniere}];[.A{numero}])', attendus[0]),
            _formule(f'SUMIFS([.H${premiere}:.H${derniere}];'
                     f'[.D${premiere}:.D${derniere}];[.A{numero}])', attendus[1]),
        ))
    dernier_lieu = premier_lieu + len(lieux) - 1

    rangees.append(_rangee(
        _texte('Total', 'gras'),
        _formule(f'SUM([.B{premier_lieu}:.B{dernier_lieu}])', dejeuners, 'gras'),
        _formule(f'SUM([.C{premier_lieu}:.C{dernier_lieu}])', diners, 'gras'),
    ))

    colonnes = ['col-large'] + ['col-moyenne'] * 7
    return _feuille(_nom_feuille(jour), colonnes, rangees), premier_lieu


def _feuille_recap(jours, lieux, premieres_rangees, seuils, genere_le):
    """Un tableau lieux × journées, pointant les totaux de chaque feuille de journée."""
    entetes = [_texte('Lieu', 'gras')]
    for jour in jours:
        entetes.append(_texte(f'{jour:%d/%m} déj.', 'gras'))
        entetes.append(_texte(f'{jour:%d/%m} dîn.', 'gras'))

    rangees = [
        _rangee(_texte('Repas à prévoir', 'titre')),
        _rangee(_texte(f'Mis à jour le {genere_le:%d/%m/%Y à %H:%M} — '
                       f'seuils : {seuils.am:g} h le matin, {seuils.pm:g} h '
                       f"l'après-midi, {seuils.jour:g} h dans la journée")),
        _rangee(),
        _rangee(*entetes),
    ]

    premiere = len(rangees) + 1
    totaux = {jour: totaux_par_lieu(lignes, seuils) for jour, lignes in jours.items()}
    for decalage, lieu in enumerate(lieux):
        cellules = [_texte(lieu)]
        for jour in jours:
            rangee_lieu = premieres_rangees[jour] + decalage
            attendus = totaux[jour].get(lieu, (0, 0))
            cellules.append(_formule(_reference(jour, f'B{rangee_lieu}'), attendus[0]))
            cellules.append(_formule(_reference(jour, f'C{rangee_lieu}'), attendus[1]))
        rangees.append(_rangee(*cellules))
    derniere = premiere + len(lieux) - 1

    totaux_colonnes = [_texte('Total', 'gras')]
    for indice, jour in enumerate(jours):
        for repas in range(2):
            colonne = chr(ord('B') + indice * 2 + repas)
            attendu = sum(compte[repas] for compte in totaux[jour].values())
            totaux_colonnes.append(
                _formule(f'SUM([.{colonne}{premiere}:.{colonne}{derniere}])', attendu, 'gras'))
    rangees.append(_rangee(*totaux_colonnes))

    colonnes = ['col-large'] + ['col-moyenne'] * (2 * len(jours))
    return _feuille('Récap', colonnes, rangees)


def _feuille_variables(seuils):
    """Les trois seuils, nommés : c'est ici que l'équipe ajuste les règles."""
    rangees = [
        _rangee(_texte('Variables', 'titre')),
        _rangee(_texte('nbHeureAM'), _nombre(seuils.am),
                _texte('heures le matin ouvrant droit au déjeuner')),
        _rangee(_texte('nbHeurePM'), _nombre(seuils.pm),
                _texte("heures l'après-midi ouvrant droit au dîner")),
        _rangee(_texte('nbHeureJour'), _nombre(seuils.jour),
                _texte('heures dans la journée ouvrant droit aux deux repas')),
        _rangee(),
        _rangee(_texte('Modifier une de ces valeurs recalcule tout le classeur.')),
        _rangee(_texte('Le fichier est régénéré automatiquement : une modification '
                       'sera écrasée à la prochaine mise à jour.')),
    ]
    return _feuille('Variables', ['col-large', 'col-moyenne', 'col-large'], rangees)


def _plages_nommees():
    """`nbHeureAM` & co. — les noms utilisés par les formules des feuilles de journée."""
    noms = ''.join(
        f'<table:named-range table:name="{nom}" '
        f'table:base-cell-address="$Variables.$B${rangee}" '
        f'table:cell-range-address="$Variables.$B${rangee}"/>'
        for nom, rangee in (('nbHeureAM', 2), ('nbHeurePM', 3), ('nbHeureJour', 4)))
    return f'<table:named-expressions>{noms}</table:named-expressions>'


# === Assemblage ===

def _contenu(jours, seuils, genere_le):
    lieux = _lieux_du_classeur(jours)
    feuilles = []
    premieres_rangees = {}
    for jour, lignes in jours.items():
        xml, premiere = _feuille_journee(jour, lignes, lieux, seuils)
        feuilles.append(xml)
        premieres_rangees[jour] = premiere

    corps = (_feuille_recap(jours, lieux, premieres_rangees, seuils, genere_le)
             + ''.join(feuilles) + _feuille_variables(seuils) + _plages_nommees())

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
            f'<dc:title>Repas à prévoir</dc:title>'
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


def ecrire_classeur(chemin, jours, seuils=SEUILS_DEFAUT, genere_le=None):
    """Écrit le classeur des repas.

    Args:
        chemin: fichier `.ods` à écrire (écrasé s'il existe)
        jours: {date: [Ligne]} — cf. `calcul.tableau()`
        seuils: les trois variables, recopiées dans la feuille « Variables »
        genere_le: horodatage affiché dans le récapitulatif (maintenant par défaut)
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
        archive.writestr('content.xml', _contenu(jours, seuils, genere_le))
        archive.writestr('styles.xml', _styles())
        archive.writestr('meta.xml', _meta(genere_le))
    return chemin
