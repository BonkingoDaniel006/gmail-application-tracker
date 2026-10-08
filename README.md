# gmail-application-tracker

Après un scan, l'application propose de relancer les adresses qui n'ont jamais
répondu ou de revenir au menu principal. Un seul message est envoyé par adresse
et l'envoi demande une confirmation explicite.

Avant l'envoi, renseignez `prenom`, `nom`, `telephone` et `lien` dans
[`mail.json`](./mail.json). Le fichier `Daniel.pdf` doit rester à côté de
`script.py`. Le sujet et le texte du message se modifient aussi dans
`mail.json`.

Le premier lancement après l'ajout du droit d'envoi Gmail demandera une
nouvelle autorisation dans le navigateur. Le jeton OAuth est ensuite mis à
jour dans `token.json`.
