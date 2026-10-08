import json
from datetime import datetime
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path
import time
import base64
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# La lecture sert au scan ; l'envoi est nécessaire pour les relances.
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
]
BASE_DIR = Path(__file__).resolve().parent


def get_gmail_service():
    """Gère l'authentification OAuth2 et retourne le service Gmail."""
    creds = None
    token_path = BASE_DIR / 'token.json'
    credentials_path = BASE_DIR / 'credentials.json'
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    
    missing_scopes = creds is not None and not creds.has_scopes(SCOPES)
    if not creds or not creds.valid or missing_scopes:
        if creds and creds.expired and creds.refresh_token and not missing_scopes:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                print("\n[ERREUR] Le fichier 'credentials.json' est introuvable.")
                print("Veuillez télécharger vos identifiants Google Cloud Console et les placer dans le même dossier.\n")
                raise FileNotFoundError(credentials_path)
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_path), SCOPES
            )
            creds = flow.run_local_server(port=0)
        
        with token_path.open('w', encoding='utf-8') as token:
            token.write(creds.to_json())

    return build('gmail', 'v1', credentials=creds)


def entetes_message(message):
    """Retourne les en-têtes du message indexés sans tenir compte de la casse."""
    return {
        header.get('name', '').lower(): header.get('value', '')
        for header in message.get('payload', {}).get('headers', [])
    }


def date_message(message):
    """Formate l'horodatage Gmail pour l'affichage et le modèle de relance."""
    date = datetime.fromtimestamp(int(message['internalDate']) / 1000)
    return date.astimezone().strftime('%d/%m/%Y')


def lister_messages(service, query):
    """Récupère les résultats d'une recherche Gmail en suivant la pagination."""
    resultats = []
    page_token = None

    while True:
        response = service.users().messages().list(
            userId='me',
            q=query,
            pageToken=page_token,
            maxResults=500
        ).execute(num_retries=5)
        resultats.extend(response.get('messages', []))
        page_token = response.get('nextPageToken')
        if not page_token:
            return resultats


def extraire_destinataires(message):
    """Extrait les adresses du champ To d'une candidature envoyée."""
    en_tetes = message.get('payload', {}).get('headers', [])
    valeurs = [
        header.get('value', '')
        for header in en_tetes
        if header.get('name', '').lower() == 'to'
    ]
    return {
        adresse.lower()
        for _, adresse in getaddresses(valeurs)
        if adresse and '@' in adresse
    }


def lister_adresses_entete(message, nom_entete):
    """Extrait les adresses d'un en-tête comme To ou From."""
    valeurs = [
        header.get('value', '')
        for header in message.get('payload', {}).get('headers', [])
        if header.get('name', '').lower() == nom_entete.lower()
    ]
    return {
        adresse.lower()
        for _, adresse in getaddresses(valeurs)
        if adresse and '@' in adresse
    }


def lire_metadonnees(service, message_id, cache):
    """Lit et met en cache les métadonnées nécessaires d'un message."""
    if message_id not in cache:
        cache[message_id] = service.users().messages().get(
            userId='me',
            id=message_id,
            format='metadata',
            metadataHeaders=['To', 'From', 'Subject']
        ).execute(num_retries=5)
        time.sleep(1)
    return cache[message_id]


def rechercher_reponses(service, candidatures, cache):
    """Associe chaque réponse à son expéditeur et construit le dictionnaire de relance."""
    destinataires = {}
    adresses_personnelles = set()
    messages_par_id = {}

    for candidature in candidatures:
        metadata = lire_metadonnees(service, candidature['id'], cache)
        messages_par_id[candidature['id']] = metadata
        date_envoi = int(metadata['internalDate'])
        adresses_personnelles.update(lister_adresses_entete(metadata, 'From'))

        for adresse in extraire_destinataires(metadata):
            destinataire = destinataires.setdefault(
                adresse,
                {'premier_envoi': date_envoi, 'dernier_envoi': date_envoi,
                 'derniere_candidature': metadata}
            )
            destinataire['premier_envoi'] = min(
                destinataire['premier_envoi'], date_envoi
            )
            if date_envoi >= destinataire['dernier_envoi']:
                destinataire['dernier_envoi'] = date_envoi
                destinataire['derniere_candidature'] = metadata

    reponses = {}
    adresses_candidats = sorted(set(destinataires) - adresses_personnelles)
    for debut in range(0, len(adresses_candidats), 20):
        groupe = adresses_candidats[debut:debut + 20]
        filtres = ' '.join(f'from:{adresse}' for adresse in groupe)
        query = (
            f'in:inbox from:{groupe[0]}'
            if len(groupe) == 1
            else f'in:inbox {{{filtres}}}'
        )

        for message in lister_messages(service, query):
            metadata = lire_metadonnees(service, message['id'], cache)
            date_reception = int(metadata['internalDate'])
            for adresse in lister_adresses_entete(metadata, 'From'):
                destinataire = destinataires.get(adresse)
                if (
                    destinataire
                    and adresse not in adresses_personnelles
                    and date_reception > destinataire['premier_envoi']
                ):
                    reponses[message['id']] = metadata
                    break

    adresses_avec_reponse = {
        adresse
        for message in reponses.values()
        for adresse in lister_adresses_entete(message, 'From')
    }
    relances = {
        adresse: date_message(info['derniere_candidature'])
        for adresse, info in destinataires.items()
        if adresse not in adresses_personnelles
        and adresse not in adresses_avec_reponse
    }

    return reponses, relances, messages_par_id


def afficher_resultats(candidatures, reponses, messages_par_id):
    """Affiche uniquement les quatre statistiques et les deux mails les plus anciens."""
    print(f"Candidatures envoyées : {len(candidatures)}")

    if candidatures:
        plus_ancienne = min(
            (messages_par_id[item['id']] for item in candidatures),
            key=lambda message: int(message['internalDate'])
        )
        headers = entetes_message(plus_ancienne)
        print(
            "Plus vieille candidature : "
            f"{date_message(plus_ancienne)} — "
            f"{headers.get('to', '(destinataire inconnu)')} — "
            f"{headers.get('subject', '(sans objet)')}"
        )
    else:
        print("Plus vieille candidature : aucune")

    print(f"Réponses reçues : {len(reponses)}")
    if reponses:
        plus_ancienne_reponse = min(
            reponses.values(),
            key=lambda message: int(message['internalDate'])
        )
        headers = entetes_message(plus_ancienne_reponse)
        print(
            "Plus vieille réponse : "
            f"{date_message(plus_ancienne_reponse)} — "
            f"{headers.get('from', '(expéditeur inconnu)')} — "
            f"{headers.get('subject', '(sans objet)')}"
        )
    else:
        print("Plus vieille réponse : aucune")


def scanner_candidatures(service):
    """Compte les candidatures/réponses et renvoie les adresses à relancer."""
    query = 'in:sent subject:"candidature" subject:"Full-stack"'

    try:
        candidatures = lister_messages(service, query)
        cache = {}
        reponses, relances, messages_par_id = rechercher_reponses(
            service, candidatures, cache
        )
        afficher_resultats(candidatures, reponses, messages_par_id)
        return relances
    except HttpError as error:
        print(f"\n[ERREUR API] Le scan a échoué : {error}\n")
        return None


def charger_configuration_mail():
    """Charge et valide le modèle de relance et les coordonnées dans mail.json."""
    config_path = BASE_DIR / 'mail.json'
    with config_path.open(encoding='utf-8') as fichier:
        configuration = json.load(fichier)

    champs_requis = ('subject', 'body', 'prenom', 'nom', 'telephone', 'lien',
                     'attachment')
    if any(
        not isinstance(configuration.get(champ), str)
        or not configuration[champ].strip()
        for champ in champs_requis
    ):
        raise ValueError(
            "mail.json doit contenir subject, body, prenom, nom, telephone, "
            "lien et attachment ; les coordonnées ne doivent pas être vides."
        )

    chemin_piece_jointe = BASE_DIR / configuration['attachment']
    if not chemin_piece_jointe.is_file():
        raise FileNotFoundError(
            f"Pièce jointe introuvable : {chemin_piece_jointe}"
        )

    return configuration, chemin_piece_jointe


def envoyer_relances(service, relances, configuration, chemin_piece_jointe):
    """Envoie une seule relance par adresse sans réponse."""
    envoyes = 0
    subject = configuration['subject']

    for adresse, date_candidature in relances.items():
        requete_precedent = (
            f'in:sent to:{adresse} subject:"{subject}"'
        )
        if lister_messages(service, requete_precedent):
            print(f"Relance déjà envoyée à {adresse}, ignorée.")
            continue

        message = EmailMessage()
        message['To'] = adresse
        message['Subject'] = subject
        try:
            message.set_content(configuration['body'].format(
                date_candidature=date_candidature,
                prenom=configuration['prenom'],
                nom=configuration['nom'],
                telephone=configuration['telephone'],
                lien=configuration['lien'],
                portfolio=configuration['portfolio'],
            ))
        except KeyError as error:
            raise ValueError(
                f"Champ de modèle inconnu dans mail.json : {error}"
            ) from error

        message.add_attachment(
            chemin_piece_jointe.read_bytes(),
            maintype='application',
            subtype='pdf',
            filename=chemin_piece_jointe.name
        )
        raw_message = base64.urlsafe_b64encode(
            message.as_bytes()
        ).decode('ascii').rstrip('=')

        try:
            service.users().messages().send(
                userId='me',
                body={'raw': raw_message}
            ).execute(num_retries=5)
            envoyes += 1
            print(f"Relance envoyée à {adresse}.")
            time.sleep(1)
        except HttpError as error:
            print(
                f"\n[ERREUR API] Envoi interrompu pour {adresse} : {error}. "
                f"{envoyes} relance(s) envoyée(s) avant l'erreur.\n"
            )
            return envoyes

    print(f"\nRelances envoyées : {envoyes}")
    return envoyes

def menu():
    """Affiche le menu interactif de l'application."""
    print("\n==================================================")
    print("   Bienvenue dans l'Automatisation Relance Mail")
    print("==================================================")
    print("1. Commencer le scan")
    print("2. Arrêter le programme")
    print("--------------------------------------------------")

def main():
    service = None
    
    while True:
        menu()
        choix = input("Faites votre choix (1 ou 2) : ").strip()

        if choix == '1':
            if not service:
                print("\n[INFO] Vérification de l'authentification Google...")
                service = get_gmail_service()
            relances = scanner_candidatures(service)
            if relances is None:
                continue

            while True:
                print("\n1. Relancer les destinataires qui n'ont jamais répondu")
                print("2. Retourner au menu principal")
                choix_scan = input("Votre choix (1 ou 2) : ").strip()

                if choix_scan == '2':
                    break
                if choix_scan != '1':
                    print("[ATTENTION] Choix invalide. Veuillez saisir 1 ou 2.")
                    continue
                if not relances:
                    print("Aucun destinataire sans réponse à relancer.")
                    break

                try:
                    configuration, piece_jointe = charger_configuration_mail()
                except (OSError, ValueError) as error:
                    print(f"[ERREUR] Configuration de relance invalide : {error}")
                    break

                print(
                    f"{len(relances)} destinataire(s) n'ont jamais répondu. "
                    "Chaque adresse recevra une seule relance avec Daniel.pdf."
                )
                confirmation = input(
                    "Pour confirmer l'envoi, tapez ENVOYER "
                    "(ou toute autre touche pour annuler) : "
                ).strip()
                if confirmation != 'ENVOYER':
                    print("Envoi annulé.")
                    break

                envoyer_relances(service, relances, configuration, piece_jointe)
                break
        elif choix == '2':
            print("\n[FERMETURE] Arrêt du programme. Bon courage pour tes recherches d'alternance !")
            return
        else:
            print("\n[ATTENTION] Choix invalide. Veuillez saisir 1 ou 2.")

if __name__ == '__main__':
    main()