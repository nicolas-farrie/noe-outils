# noe-outils

Petits outils d'exploitation des données de [NOÉ](https://noe-app.io/) (bénévolat
d'événements), pour les besoins que NOÉ ne couvre pas lui-même.

Le principe : **lire** l'API NOÉ, **calculer**, **déposer** un fichier (`.ods`) dans
Seafile, où l'équipe l'ouvre depuis son téléphone ou son ordinateur. Pas d'application à
installer, pas de base de données locale — tant que les besoins restent de cet ordre.

## Outils

- **`catering/`** — pour chaque demi-journée et chaque lieu, combien de bénévoles font
  au moins 4 h de présence, afin de prévoir les repas. *(à venir)*

## Origine

Né d'une demande de l'équipe d'un festival utilisant NOÉ. Volontairement séparé de
[Contact Mailer](https://github.com/nicolas-farrie/contact-mailer) : ces outils
produisent de la logistique (présences, horaires, lieux), pas de la gestion de contacts.

`noe.py` (client de l'API NOÉ) vient de Contact Mailer, copié tel quel : il n'y dépend de
rien. Les deux copies peuvent diverger — on factorisera en paquet commun le jour où elles
évolueront toutes les deux.
