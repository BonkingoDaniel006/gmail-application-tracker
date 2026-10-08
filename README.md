# Suivi et relance de candidatures Gmail

Petit outil en ligne de commande qui analyse les candidatures envoyées depuis
Gmail, repère les réponses reçues et peut envoyer un message de relance aux
adresses pour lesquelles aucune réponse n'a été détectée. Il est conçu pour
éviter de relancer manuellement chaque candidature.

## Fonctionnalités

- Recherche les messages envoyés dont l'objet contient **« candidature »** et
  **« Full-stack »**.
- Affiche le nombre de candidatures trouvées, la plus ancienne, le nombre de
  réponses détectées et la plus ancienne réponse.
- Cherche une réponse dans la boîte de réception lorsqu'elle provient d'une
  adresse à laquelle une candidature a été envoyée et qu'elle est postérieure
  à la première candidature envoyée à cette adresse.
- Prépare une relance unique par adresse sans réponse détectée, avec le texte
  configuré dans `mail.json` et le PDF `Daniel.pdf`.
- N'envoie rien sans confirmation explicite : il faut saisir `ENVOYER`.
- Ignore une adresse si une relance ayant le même objet est déjà présente dans
  les messages envoyés.

## Prérequis

- Python 3
- Un compte Google avec Gmail
- L'API Gmail activée dans un projet Google Cloud
- Les fichiers `credentials.json`, `mail.json` et `Daniel.pdf`

## Installation

Depuis le dossier du projet, crée et active un environnement virtuel :

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Installe les bibliothèques nécessaires :

```bash
python -m pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
```

## Configuration de Google Gmail

1. Dans Google Cloud Console, crée ou sélectionne un projet et active **Gmail
   API**.
2. Configure l'écran de consentement OAuth. Si l'application est en mode test,
   ajoute le compte Gmail utilisé comme utilisateur test.
3. Crée un identifiant OAuth de type **Application de bureau** et télécharge le
   fichier JSON.
4. Place ce fichier à la racine du projet sous le nom `credentials.json`.
5. Lance le programme. Au premier lancement, un navigateur s'ouvre pour
   sélectionner le compte et autoriser l'accès Gmail.

Le programme demande les autorisations Gmail suivantes :

- `gmail.readonly` pour rechercher et lire les messages ;
- `gmail.send` pour envoyer les relances après confirmation.

Le jeton OAuth est conservé dans `token.json` et réutilisé lors des lancements
suivants. Si les autorisations demandées changent, une nouvelle autorisation
Google peut être nécessaire.

## Préparer le modèle de relance

Modifie `mail.json` avant d'utiliser l'envoi :

- `subject` : objet des relances ;
- `body` : texte du message ;
- `prenom`, `nom`, `telephone`, `lien`, `portfolio` : coordonnées insérées dans
  le texte ;
- `attachment` : nom du PDF à joindre (par défaut `Daniel.pdf`).

Le corps du message utilise ces champs comme marqueurs, par exemple
`{date_candidature}`, `{prenom}`, `{nom}`, `{telephone}`, `{lien}` et
`{portfolio}`. Le fichier PDF doit se trouver à la racine du projet, à côté de
`script.py`.

## Utilisation

```bash
python script.py
```

Dans le menu :

1. Choisis **Commencer le scan**.
2. Lis les statistiques affichées.
3. Choisis **Relancer les destinataires qui n'ont jamais répondu** ou retourne
   au menu principal.
4. Vérifie le nombre de destinataires, puis saisis exactement `ENVOYER` pour
   confirmer l'envoi.

Le programme affiche des indicateurs de progression pendant le scan. Les
requêtes Gmail sont paginées et des tentatives automatiques sont utilisées pour
les appels API.

## Limites à connaître

- Le scan des candidatures dépend du filtre d'objet : il ne trouve pas les
  candidatures dont l'objet ne contient pas les deux expressions recherchées.
- Les réponses sont recherchées dans la **boîte de réception** (`in:inbox`).
  Un message archivé ou déplacé hors de la boîte de réception peut ne pas être
  détecté.
- Une adresse est considérée comme ayant répondu si un message reçu de cette
  adresse est postérieur à la première candidature envoyée à cette adresse.
  Une réponse envoyée depuis une autre adresse ne sera pas associée
  automatiquement.
- Le compte affiché comme expéditeur des candidatures est exclu des adresses à
  relancer.
- Le comptage des réponses porte sur les messages reçus correspondants, pas
  nécessairement sur le nombre d'entreprises ayant répondu.
- La relance reprend la date de la dernière candidature trouvée pour chaque
  adresse.

Vérifie les destinataires, le contenu de `mail.json` et la pièce jointe avant de
confirmer tout envoi.

## Fichiers locaux et confidentialité

`credentials.json` contient les identifiants OAuth de l'application et
`token.json` contient les jetons d'accès au compte. Ne publie ni ne partage ces
fichiers. Ils sont exclus du dépôt par `.gitignore`. Le fichier `mail.json`
contient également des coordonnées personnelles : garde-le privé si tu publies
le projet.
