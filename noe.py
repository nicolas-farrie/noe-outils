"""Client API NOÉ et synchronisation des bénévoles d'un projet.

NOÉ (https://noe-app.io, AGPLv3) gère le bénévolat d'un événement : pôles, missions,
créneaux. Ce module alimente les listes de contact-mailer depuis les inscrits d'un
projet NOÉ — l'application n'ayant pas d'envoi d'emails groupés, c'est le maillon
qu'elle délègue à des outils tiers (comme elle le fait déjà pour les SMS par webhook).

Modèle NOÉ, à connaître pour comprendre le croisement ci-dessous :

    Category « Restauration » → Activity « Service du midi » → Session « samedi 12h-15h »
        → SessionSubscription → Registration → User (email, prénom, nom)

L'appartenance à un pôle n'est donc pas un champ : elle se déduit des créneaux auxquels
le bénévole s'est inscrit. Et comme les filtres de l'API ne traversent pas les
références (pas de `filter[activity.name]=…`), le croisement se fait ici, côté client,
en quelques appels — cf. build_group_index().

Sens unique, volontairement : NOÉ → contact-mailer. Créer une inscription rattacherait
la personne au projet, jamais à un pôle ; pré-affecter quelqu'un supposerait de choisir
ses créneaux à sa place, ce que NOÉ est précisément fait pour laisser au bénévole.
"""

import re
import requests

# Groupe synthétique, en plus des pôles : sans lui, les inscrits n'ayant pas encore
# choisi de créneau n'appartiennent à aucune catégorie et sortent du champ du
# connecteur — or ce sont souvent eux qu'il faut relancer. Relevé sur le festival des
# Fourmilières : 23 inscrits sur 40 dans ce cas.
GROUP_ALL = 'Tous les inscrits'


class NoeClient:
    """Client pour l'API REST NOÉ (un projet = un événement)."""

    def __init__(self, base_url, token, project_id):
        self.base_url = base_url.rstrip('/')
        self.project_id = project_id
        self.session = requests.Session()
        # NOÉ attend « JWT <token> », pas « Bearer ». Le jeton se récupère dans
        # l'interface (Mon compte → Jeton d'API) et vaut plus d'un an.
        self.session.headers.update({
            'Authorization': f'JWT {token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        })

    def _request(self, method, endpoint, **kwargs):
        """Requête centralisée avec gestion d'erreurs."""
        url = f'{self.base_url}/{endpoint.lstrip("/")}'
        try:
            resp = self.session.request(method, url, timeout=30, **kwargs)
        except requests.ConnectionError:
            raise RuntimeError(f'Impossible de se connecter à {self.base_url}')
        except requests.Timeout:
            raise RuntimeError(f'Timeout lors de la requête vers {self.base_url}')

        if resp.status_code == 401:
            raise RuntimeError('Authentification NOÉ refusée (jeton invalide ou expiré)')
        if resp.status_code == 403:
            raise RuntimeError('Permission NOÉ insuffisante (compte organisateur requis)')
        if resp.status_code == 404:
            raise RuntimeError(f'Ressource NOÉ introuvable : {endpoint}')
        if not resp.ok:
            try:
                detail = resp.json()
                msg = detail.get('message') or detail.get('error') or str(detail)[:200]
            except Exception:
                msg = resp.text[:200]
            raise RuntimeError(f'NOÉ {resp.status_code}: {msg}')
        return resp.json()

    def _list(self, endpoint, page_size=200):
        """Retourne tous les éléments d'un endpoint LIST (pagination limit/skip).

        L'API n'est pas homogène : la plupart des endpoints renvoient une liste brute,
        mais /sessions renvoie {"list": [...], "allLoaded": bool}. Les deux formes sont
        acceptées ici pour que l'appelant n'ait pas à s'en soucier.
        """
        items = []
        skip = 0
        while True:
            sep = '&' if '?' in endpoint else '?'
            data = self._request('GET', f'{endpoint}{sep}limit={page_size}&skip={skip}')
            batch = data.get('list', []) if isinstance(data, dict) else data
            if not batch:
                break
            items.extend(batch)
            if len(batch) < page_size:
                break
            skip += page_size
        return items

    def _project_endpoint(self, name):
        return f'projects/{self.project_id}/{name}'

    # === LECTURE ===

    def get_project(self):
        """GET /projects/{id} — dont formComponents (définition du formulaire)."""
        return self._request('GET', f'projects/{self.project_id}')

    def list_categories(self):
        """Les pôles : « Accueil », « Restauration », « Régie technique »…"""
        return self._list(self._project_endpoint('categories'))

    def list_activities(self):
        """Les missions, chacune rattachée à une catégorie."""
        return self._list(self._project_endpoint('activities'))

    def list_sessions(self):
        """Les créneaux datés, chacun rattaché à une activité."""
        return self._list(self._project_endpoint('sessions'))

    def list_registrations(self):
        """Les inscriptions au projet (avec user et sessionsSubscriptions)."""
        return self._list(self._project_endpoint('registrations'))

    def get_registration_schema(self):
        """GET …/registrations/schema — description des champs, champs perso compris."""
        return self._request('GET', self._project_endpoint('registrations/schema'))

    # === FORMULAIRE D'INSCRIPTION ===

    def form_fields(self, project=None):
        """{clé: {'label', 'type'}} pour tous les champs du formulaire d'inscription.

        Les réponses vivent dans `registration.formAnswers`, sous des clés suffixées
        d'un identifiant généré (`numeroDeTel_6pq`) : impossible à deviner, il faut
        donc lire la définition du projet. Les panels imbriquent leurs champs dans
        `components`, d'où le parcours récursif.
        """
        project = project or self.get_project()
        fields = {}

        def walk(components):
            for c in components or []:
                if not isinstance(c, dict):
                    continue
                key, ctype = c.get('key'), c.get('type')
                # `content` = bloc de texte décoratif, `panel` = conteneur : ni l'un ni
                # l'autre ne porte de réponse.
                if key and ctype not in ('content', 'panel'):
                    fields[key] = {'label': c.get('label') or key, 'type': ctype}
                walk(c.get('components'))

        walk(project.get('formComponents'))
        return fields

    def phone_field_key(self, project=None):
        """Clé du champ téléphone, repérée par son type — pas par son nom.

        NOÉ ne stocke pas de téléphone sur le compte utilisateur : quand il existe,
        c'est une réponse au formulaire. Le type `phoneNumber` l'identifie de façon
        stable d'un festival à l'autre, là où la clé change à chaque fois.
        """
        for key, meta in self.form_fields(project).items():
            if meta.get('type') == 'phoneNumber':
                return key
        return None


def _ident(value):
    """L'id d'une référence, qu'elle soit peuplée ({_id: …}) ou brute ("…")."""
    return value.get('_id') if isinstance(value, dict) else value


def _normalize_phone(value):
    """Trim et espaces réduits ; le format reste celui saisi par le bénévole."""
    if not isinstance(value, str):
        return ''
    return re.sub(r'\s+', ' ', value).strip()


def build_group_index(client, level='category', project=None):
    """{nom du groupe: [contacts]} — les pôles, plus GROUP_ALL.

    Args:
        client: NoeClient configuré
        level: 'category' (les pôles, défaut) ou 'activity' (les missions, plus fin)
        project: projet déjà chargé, pour éviter de le redemander quand l'appelant en a
                 besoin par ailleurs (son nom, par exemple)

    Returns:
        dict {nom: liste de dicts {email, prenom, nom, telephone, groupes}}

    Coût : 4 requêtes (projet, catégories, activités, sessions) + les inscriptions,
    quel que soit le nombre de bénévoles — le croisement est fait en mémoire.
    """
    if level not in ('category', 'activity'):
        raise ValueError("level doit valoir 'category' ou 'activity'")

    project = project or client.get_project()
    phone_key = client.phone_field_key(project)

    categories = {c['_id']: c.get('name', '(sans nom)') for c in client.list_categories()}
    activities = {a['_id']: a for a in client.list_activities()}

    # Chaque créneau ramené au nom de son groupe, pour ne pas refaire la chaîne
    # session → activité → catégorie une fois par inscription.
    session_group = {}
    for s in client.list_sessions():
        activity = activities.get(_ident(s.get('activity')))
        if not activity:
            continue
        if level == 'activity':
            session_group[s['_id']] = activity.get('name', '(sans nom)')
        else:
            session_group[s['_id']] = categories.get(
                _ident(activity.get('category')), '(sans catégorie)')

    # Seuls les groupes ayant au moins un bénévole. Un festival déclare beaucoup de
    # catégories très en amont (16 ici, dont 12 sans personne) : les lister toutes
    # noierait celles où il y a quelqu'un à qui écrire. Choix confirmé par l'équipe du
    # festival — la page sert à communiquer, pas à piloter le recrutement.
    groups = {}

    for reg in client.list_registrations():
        user = reg.get('user')
        if not isinstance(user, dict):
            continue  # référence non peuplée : pas d'email exploitable
        email = (user.get('email') or '').strip()
        if not email:
            continue

        names = set()
        for sub in (reg.get('sessionsSubscriptions') or []):
            name = session_group.get(_ident(sub.get('session')))
            if name:
                names.add(name)

        contact = {
            'email': email,
            'prenom': (user.get('firstName') or '').strip(),
            'nom': (user.get('lastName') or '').strip(),
            'telephone': _normalize_phone(
                (reg.get('formAnswers') or {}).get(phone_key)) if phone_key else '',
            'groupes': sorted(names),
            # Identité NOÉ de la personne, pour l'apparier durablement côté
            # contact-mailer (cf. ExternalIdentity) : c'est elle qui rend les
            # synchronisations suivantes idempotentes, là où l'email peut changer.
            # L'utilisateur plutôt que l'inscription : une même personne a une
            # inscription par projet, mais un seul compte.
            'ext_id': user.get('_id') or '',
        }

        # GROUP_ALL rassemble tout le monde, y compris qui n'a pas encore de créneau.
        for name in list(names) + [GROUP_ALL]:
            groups.setdefault(name, []).append(contact)

    groups.setdefault(GROUP_ALL, [])   # projet sans aucun inscrit : le groupe existe quand même

    return groups


def pull_contacts_from_noe(client, group_name, level='category'):
    """Les contacts d'un groupe NOÉ, prêts à alimenter une liste contact-mailer.

    Returns:
        (contacts, stats) — stats porte le total et le nombre de groupes disponibles,
        pour distinguer « groupe vide » de « groupe inexistant » dans l'interface.
    """
    index = build_group_index(client, level=level)
    contacts = index.get(group_name, [])
    return contacts, {
        'group': group_name,
        'found': len(contacts),
        'groups_available': sorted(index.keys()),
    }
