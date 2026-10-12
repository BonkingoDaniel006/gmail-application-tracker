import base64
import csv
import json
import re
import sys
import time
from email.message import EmailMessage
from io import StringIO
from pathlib import Path

from googleapiclient.errors import HttpError

from script import BASE_DIR, get_gmail_service


FICHIER_CONTACTS = BASE_DIR / "contacts.csv"
FICHIER_CONFIGURATION = BASE_DIR / "spontané.json"
EMAIL_RE = re.compile(
    r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+",
    re.IGNORECASE,
)


def charger_destinataires(chemin: Path = FICHIER_CONTACTS) -> list[str]:
    """Lit uniquement la colonne d'adresses placée sous CONTACT RH."""
    contenu = chemin.read_bytes()
    try:
        texte = contenu.decode("utf-8-sig")
    except UnicodeDecodeError:
        texte = contenu.decode("iso-8859-1")

    lecteur = csv.reader(StringIO(texte), delimiter=";")
    entete_groupes = next(lecteur, [])
    entete_champs = next(lecteur, [])
    if (
        len(entete_groupes) <= 5
        or entete_groupes[0].strip().upper() != "ENTREPRISE"
        or entete_groupes[4].strip().upper() != "CONTACT RH"
        or len(entete_champs) <= 5
        or entete_champs[5].strip().upper() != "MAIL"
    ):
        raise ValueError(
            "Structure inattendue dans contacts.csv : la colonne des adresses "
            "RH n'a pas été identifiée avec certitude."
        )

    destinataires = {}
    lignes_invalides = []
    for numero_ligne, ligne in enumerate(lecteur, start=3):
        if not ligne or not any(cell.strip() for cell in ligne):
            continue
        if len(ligne) <= 5:
            lignes_invalides.append(numero_ligne)
            continue

        champ_rh = ligne[5].strip()
        adresses = {
            match.group(0).rstrip(".").lower()
            for match in EMAIL_RE.finditer(champ_rh)
        }
        email_invalide = "@" in EMAIL_RE.sub("", champ_rh)
        contact_sans_adresse = len(ligne) > 4 and ligne[4].strip() and not champ_rh
        if contact_sans_adresse or (champ_rh and (not adresses or email_invalide)):
            lignes_invalides.append(numero_ligne)
        for adresse in sorted(adresses):
            destinataires.setdefault(adresse, None)

    if lignes_invalides:
        lignes = ", ".join(str(numero) for numero in lignes_invalides)
        raise ValueError(
            "Adresse RH absente ou invalide aux lignes "
            f"{lignes} de contacts.csv. Corrige ces lignes avant l'envoi."
        )
    if not destinataires:
        raise ValueError("Aucune adresse RH valide trouvée dans contacts.csv.")
    return list(destinataires)


def charger_configuration() -> tuple[dict, Path]:
    """Charge et valide le modèle du message et le PDF à joindre."""
    with FICHIER_CONFIGURATION.open(encoding="utf-8") as fichier:
        configuration = json.load(fichier)

    champs_requis = (
        "subject",
        "body",
        "prenom",
        "nom",
        "titre",
        "telephone",
        "lien",
        "portfolio",
        "attachment",
    )
    if any(
        not isinstance(configuration.get(champ), str)
        or not configuration[champ].strip()
        for champ in champs_requis
    ):
        raise ValueError(
            f"{FICHIER_CONFIGURATION.name} doit contenir les champs "
            f"non vides suivants : {', '.join(champs_requis)}."
        )

    piece_jointe = BASE_DIR / configuration["attachment"]
    if not piece_jointe.is_file():
        raise FileNotFoundError(f"Pièce jointe introuvable : {piece_jointe}")
    if piece_jointe.suffix.lower() != ".pdf":
        raise ValueError("La pièce jointe configurée doit être un fichier PDF.")
    return configuration, piece_jointe


def creer_message(
    adresse: str, configuration: dict, piece_jointe: Path
) -> EmailMessage:
    message = EmailMessage()
    message["To"] = adresse
    message["Subject"] = configuration["subject"]
    try:
        message.set_content(
            configuration["body"].format(
                prenom=configuration["prenom"],
                nom=configuration["nom"],
                titre=configuration["titre"],
                telephone=configuration["telephone"],
                lien=configuration["lien"],
                portfolio=configuration["portfolio"],
            )
        )
    except KeyError as error:
        raise ValueError(
            f"Champ de modèle inconnu dans {FICHIER_CONFIGURATION.name} : "
            f"{error}"
        ) from error

    message.add_attachment(
        piece_jointe.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=piece_jointe.name,
    )
    return message


def envoyer(service, destinataires: list[str], configuration: dict, pdf: Path):
    """Envoie un message distinct à chaque adresse RH après confirmation."""
    envoyes = 0
    for adresse in destinataires:
        message = creer_message(adresse, configuration, pdf)
        message_brut = base64.urlsafe_b64encode(message.as_bytes()).decode(
            "ascii"
        ).rstrip("=")
        try:
            service.users().messages().send(
                userId="me", body={"raw": message_brut}
            ).execute(num_retries=5)
        except HttpError as error:
            raise RuntimeError(
                f"Échec de l'envoi à {adresse}. "
                f"{envoyes} message(s) envoyé(s) avant l'arrêt."
            ) from error
        envoyes += 1
        print(f"[ENVOI] Message envoyé à {adresse}.")
        time.sleep(1)

    print(f"[TERMINÉ] {envoyes} message(s) envoyé(s) aux RH.")


def main() -> None:
    try:
        destinataires = charger_destinataires()
        configuration, piece_jointe = charger_configuration()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"[ERREUR] {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(
        f"{len(destinataires)} adresse(s) RH unique(s) seront destinataires. "
        "Aucun contact de la colonne des tuteurs ne sera utilisé."
    )
    for adresse in destinataires:
        print(f"  - {adresse}")
    confirmation = input(
        "\nTapez exactement ENVOYER pour confirmer (ou toute autre "
        "réponse pour annuler) : "
    ).strip()
    if confirmation != "ENVOYER":
        print("[ANNULÉ] Aucun message envoyé.")
        return

    try:
        service = get_gmail_service()
        envoyer(service, destinataires, configuration, piece_jointe)
    except (HttpError, OSError, RuntimeError, ValueError) as error:
        print(f"[ERREUR] {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()