import base64
from datetime import datetime
from email.utils import getaddresses
import os.path
import sys
import time
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Scope nécessaire : lecture seule des mails
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']


def get_gmail_service():
    """Gère l'authentification OAuth2 et retourne le service Gmail."""
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists('credentials.json'):
                print("\n[ERREUR] Le fichier 'credentials.json' est introuvable.")
                print("Veuillez télécharger vos identifiants Google Cloud Console et les placer dans le même dossier.\n")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    return build('gmail', 'v1', credentials=creds)


def extraire_corps(payload):
    """Retourne le texte du corps, en privilégiant le texte brut au HTML."""
    textes = []
    html = []

    def parcourir(partie):
        en_tetes = partie.get('headers', [])
        disposition = next(
            (
                header.get('value', '').lower()
                for header in en_tetes
                if header.get('name', '').lower() == 'content-disposition'
            ),
            ''
        )
        if partie.get('filename') or disposition.startswith('attachment'):
            return

        donnees = partie.get('body', {}).get('data')
        type_mime = partie.get('mimeType', '')
        if donnees and type_mime in ('text/plain', 'text/html'):
            contenu = base64.urlsafe_b64decode(donnees + '=' * (-len(donnees) % 4))
            texte = contenu.decode('utf-8', errors='replace')
            (textes if type_mime == 'text/plain' else html).append(texte)

        for sous_partie in partie.get('parts', []):
            parcourir(sous_partie)

    parcourir(payload)
    return '\n'.join(textes) if textes else '\n'.join(html)


def afficher_reponse(message, raisons):
    """Affiche le contenu d'un message reçu correspondant à une candidature."""
    payload = message.get('payload', {})
    en_tetes = {
        header.get('name', '').lower(): header.get('value', '')
        for header in payload.get('headers', [])
    }
    date_gmail = datetime.fromtimestamp(int(message['internalDate']) / 1000)
    date_gmail = date_gmail.astimezone().strftime('%d/%m/%Y %H:%M:%S %Z')
    corps = extraire_corps(payload)

    print(f"\nDe       : {en_tetes.get('from', '(expéditeur non disponible)')}")
    print(f"Objet    : {en_tetes.get('subject', '(sans objet)')}")
    print(f"Date     : {date_gmail}")
    print(f"Détection: {', '.join(raisons)}")
    print("-" * 50)
    print(corps or "(aucun corps textuel disponible)")
    print("=" * 50)


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
    """Extrait les adresses To/Cc/Bcc d'un message Gmail."""
    en_tetes = message.get('payload', {}).get('headers', [])
    valeurs = [
        header.get('value', '')
        for header in en_tetes
        if header.get('name', '').lower() in ('to', 'cc', 'bcc')
    ]
    return {
        adresse.lower()
        for _, adresse in getaddresses(valeurs)
        if adresse and '@' in adresse
    }


def rechercher_reponses(service, candidatures):
    """Trouve les mails reçus des destinataires ou dans les fils de candidature."""
    destinataires = set()
    fils_candidatures = set()

    for index, candidature in enumerate(candidatures, start=1):
        if candidature.get('threadId'):
            fils_candidatures.add(candidature['threadId'])
        print(
            f"\r[PROGRESSION] Lecture des destinataires "
            f"{index}/{len(candidatures)}...",
            end='',
            flush=True
        )
        message = service.users().messages().get(
            userId='me',
            id=candidature['id'],
            format='metadata',
            metadataHeaders=['To', 'Cc', 'Bcc']
        ).execute(num_retries=5)
        destinataires.update(extraire_destinataires(message))
        time.sleep(1)

    if candidatures:
        print()

    correspondances = {}

    # Un message reçu dans le même fil Gmail est une réponse, même si l'expéditeur
    # utilise une adresse différente de celle destinataire de la candidature.
    messages_recus = lister_messages(service, 'in:inbox')
    for message in messages_recus:
        if message.get('threadId') in fils_candidatures:
            correspondances.setdefault(message['id'], set()).add(
                'même fil que la candidature'
            )

    # Recherche aussi les nouveaux fils démarrés par une adresse déjà contactée.
    adresses = sorted(destinataires)
    taille_groupe = 20
    for debut in range(0, len(adresses), taille_groupe):
        groupe = adresses[debut:debut + taille_groupe]
        filtres_expediteur = ' '.join(f'from:{adresse}' for adresse in groupe)
        if len(groupe) == 1:
            query = f'in:inbox {filtres_expediteur}'
        else:
            query = f'in:inbox {{{filtres_expediteur}}}'
        for message in lister_messages(service, query):
            correspondances.setdefault(message['id'], set()).add(
                'expéditeur déjà contacté'
            )

    return correspondances


def scanner_candidatures(service):
    """Compte les candidatures et recherche les réponses dans la boîte de réception."""
    print("\n" + "="*50)
    print(" [SCAN] Initialisation du scan des candidatures...")
    print("="*50)
    time.sleep(1)

    # Requête de recherche Gmail : envoyés + objet contenant "candidature" ET "Full-stack"
    query = 'in:sent subject:"candidature" subject:"Full-stack"'
    print(f"[INFO] Filtre appliqué : {query}")
    print("[INFO] Connexion aux serveurs Gmail...")
    time.sleep(1)

    try:
        candidatures = lister_messages(service, query)

        print("\n" + "-"*50)
        print(
            "[RÉSULTAT FINAL] Total d'e-mails envoyés correspondant au filtre : "
            f"{len(candidatures)}"
        )
        print("-"*50 + "\n")

        if candidatures:
            correspondances = rechercher_reponses(service, candidatures)
            if correspondances:
                print(
                    f"[RÉPONSES] {len(correspondances)} mail(s) reçu(s) "
                    "correspondent à une candidature :\n"
                )
                for message_id, raisons in correspondances.items():
                    message = service.users().messages().get(
                        userId='me',
                        id=message_id,
                        format='full'
                    ).execute(num_retries=5)
                    afficher_reponse(message, sorted(raisons))
            else:
                print("[RÉSULTAT] Aucune réponse ou aucun mail correspondant trouvé.\n")
        else:
            print("[INFO] Aucune candidature ne correspond au filtre.\n")

    except HttpError as error:
        print(f"\n[ERREUR API] Une erreur s'est produite lors du scan : {error}\n")

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
            scanner_candidatures(service)
        elif choix == '2':
            print("\n[FERMETURE] Arrêt du programme. Bon courage pour tes recherches d'alternance !")
            sys.exit(0)
        else:
            print("\n[ATTENTION] Choix invalide. Veuillez saisir 1 ou 2.")

if __name__ == '__main__':
    main()