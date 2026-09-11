# noe-outils — instructions de projet

Outils d'exploitation des données de NOÉ (bénévolat d'événements). Principe : **lire**
l'API NOÉ, **calculer**, **déposer** un fichier `.ods` dans Seafile.

## Règles

- **Lecture seule sur NOÉ.** L'API permet d'écrire ; on ne s'en sert pas.
- **Données personnelles réelles.** En session, n'afficher que des comptages et des
  structures — jamais de liste nominative de bénévoles.
- Pas de code sans un « je lance » explicite de Nicolas (cf. `~/.claude/rules/`).

## Configuration

`.env` (modèle : `.env.example`), jamais versionné. Le code ne charge pas `.env` tout
seul : `set -a; . ./.env; set +a` avant de lancer, ou `python-dotenv`.

## `noe.py`

Copié de [contact-mailer](https://github.com/nicolas-farrie/contact-mailer) (commit
`c8572b8`). **Toute correction doit être reportée dans les deux dépôts** tant qu'une
bibliothèque commune n'existe pas. Manque connu : `list_places()`.

## Pièges de l'API NOÉ

- Authentification `Authorization: JWT <jeton>` — pas `Bearer`.
- `/sessions` renvoie `{"list": [...]}`, les autres endpoints une liste brute.
- Les filtres ne traversent pas les références : croiser côté client.
- L'appartenance à un pôle se déduit des créneaux souscrits
  (`registrations.sessionsSubscriptions` → session → activité → catégorie).
- Le téléphone est une réponse de formulaire : le repérer par son **type**
  `phoneNumber`, pas par sa clé.
- Documentation : `https://get.noe-app.io/en/docs/api/` (`/fr/` renvoie 404).

## Travail en cours

Analyses et cadrage dans `doc-travail/` (local, hors git) : `doc-projet.md` pour le
cadrage, `vrai-prompt-fiable.md` pour les messages longs.
