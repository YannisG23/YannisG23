"""Gmail et Google Agenda (via l'API Google, authentification OAuth sur ton compte)."""

from __future__ import annotations

import base64
import html
import re
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

from .registry import ToolContext, registry

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
]


class GoogleNotConfigured(RuntimeError):
    pass


def authorize(config: Any, interactive: bool = False):
    """Renvoie des identifiants Google valides ; ouvre le navigateur si interactive=True."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise GoogleNotConfigured(
            "Les bibliothèques Google ne sont pas installées : pip install -e \".[google]\""
        ) from exc

    creds = None
    if config.google_token.exists():
        creds = Credentials.from_authorized_user_file(str(config.google_token), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif interactive:
        if not config.google_credentials.exists():
            raise GoogleNotConfigured(
                f"Fichier OAuth manquant : place ton client OAuth Google dans {config.google_credentials}"
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(config.google_credentials), SCOPES)
        creds = flow.run_local_server(port=0)
    else:
        raise GoogleNotConfigured(
            "Gmail/Agenda ne sont pas encore connectés. Lance : python -m jarvis --setup-google"
        )
    config.google_token.write_text(creds.to_json())
    return creds


def _service(ctx: ToolContext, name: str, version: str):
    from googleapiclient.discovery import build

    return build(name, version, credentials=authorize(ctx.config), cache_discovery=False)


def _header(headers: list[dict], name: str) -> str:
    return next((h["value"] for h in headers if h["name"].lower() == name.lower()), "")


def extract_body(payload: dict) -> str:
    """Extrait le texte d'un message Gmail (préfère text/plain, sinon HTML nettoyé)."""
    plain, rich = [], []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
            if mime == "text/plain":
                plain.append(text)
            elif mime == "text/html":
                rich.append(text)
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    if plain:
        return "\n".join(plain).strip()
    text = "\n".join(rich)
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


@registry.tool(
    "Liste des e-mails Gmail. Par défaut les non lus de la boîte de réception. Accepte la "
    "syntaxe de recherche Gmail (ex. 'from:amazon', 'is:unread newer_than:2d', 'subject:facture').",
    properties={
        "query": {"type": "string", "description": "Recherche Gmail. Défaut : 'is:unread in:inbox'."},
        "max_results": {"type": "integer", "description": "Défaut 10, max 25."},
    },
)
def gmail_list(ctx: ToolContext, query: str = "is:unread in:inbox", max_results: int = 10) -> str:
    gmail = _service(ctx, "gmail", "v1")
    listing = (
        gmail.users()
        .messages()
        .list(userId="me", q=query, maxResults=max(1, min(int(max_results), 25)))
        .execute()
    )
    ids = [m["id"] for m in listing.get("messages", [])]
    if not ids:
        return f"Aucun e-mail pour « {query} »."
    lines = []
    for msg_id in ids:
        msg = (
            gmail.users()
            .messages()
            .get(userId="me", id=msg_id, format="metadata", metadataHeaders=["From", "Subject", "Date"])
            .execute()
        )
        headers = msg["payload"]["headers"]
        lines.append(
            f"id={msg_id} | {_header(headers, 'Date')} | De : {_header(headers, 'From')} | "
            f"Objet : {_header(headers, 'Subject')} | Aperçu : {html.unescape(msg.get('snippet', ''))}"
        )
    return "\n".join(lines)


@registry.tool(
    "Lit le contenu complet d'un e-mail Gmail (id obtenu via gmail_list).",
    properties={
        "message_id": {"type": "string"},
        "mark_as_read": {"type": "boolean", "description": "Défaut true."},
    },
    required=["message_id"],
)
def gmail_read(ctx: ToolContext, message_id: str, mark_as_read: bool = True) -> str:
    gmail = _service(ctx, "gmail", "v1")
    msg = gmail.users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = msg["payload"]["headers"]
    if mark_as_read:
        gmail.users().messages().modify(
            userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
        ).execute()
    body = extract_body(msg["payload"])
    if len(body) > 15000:
        body = body[:15000] + "\n… (tronqué)"
    return (
        f"De : {_header(headers, 'From')}\nÀ : {_header(headers, 'To')}\n"
        f"Date : {_header(headers, 'Date')}\nObjet : {_header(headers, 'Subject')}\n"
        f"Message-ID : {_header(headers, 'Message-ID')}\nThread : {msg.get('threadId')}\n\n{body}"
    )


@registry.tool(
    "Envoie un e-mail depuis le compte Gmail de l'utilisateur. Nécessite sa confirmation. "
    "Pour répondre à un e-mail, fournis reply_to_message_id.",
    properties={
        "to": {"type": "string", "description": "Destinataire(s), séparés par des virgules."},
        "subject": {"type": "string"},
        "body": {"type": "string", "description": "Texte brut du message."},
        "reply_to_message_id": {"type": "string", "description": "id Gmail du message auquel on répond."},
    },
    required=["to", "subject", "body"],
    confirm=lambda a: f"envoyer un e-mail à {a.get('to')} avec l'objet « {a.get('subject')} »",
)
def gmail_send(
    ctx: ToolContext, to: str, subject: str, body: str, reply_to_message_id: str = ""
) -> str:
    gmail = _service(ctx, "gmail", "v1")
    email = EmailMessage()
    email["To"] = to
    email["Subject"] = subject
    email.set_content(body)
    payload: dict[str, Any] = {}
    if reply_to_message_id:
        original = (
            gmail.users()
            .messages()
            .get(userId="me", id=reply_to_message_id, format="metadata", metadataHeaders=["Message-ID"])
            .execute()
        )
        rfc_id = _header(original["payload"]["headers"], "Message-ID")
        if rfc_id:
            email["In-Reply-To"] = rfc_id
            email["References"] = rfc_id
        payload["threadId"] = original["threadId"]
    payload["raw"] = base64.urlsafe_b64encode(email.as_bytes()).decode()
    sent = gmail.users().messages().send(userId="me", body=payload).execute()
    return f"E-mail envoyé (id={sent['id']})."


def _event_time(value: dict) -> str:
    return value.get("dateTime") or value.get("date", "")


@registry.tool(
    "Liste les événements de l'agenda Google sur une période.",
    properties={
        "days": {"type": "integer", "description": "Nombre de jours à partir d'aujourd'hui. Défaut 7."},
        "query": {"type": "string", "description": "Filtre texte optionnel."},
    },
)
def calendar_list(ctx: ToolContext, days: int = 7, query: str = "") -> str:
    cal = _service(ctx, "calendar", "v3")
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    params: dict[str, Any] = {
        "calendarId": "primary",
        "timeMin": start.isoformat(),
        "timeMax": (start + timedelta(days=max(1, int(days)))).isoformat(),
        "singleEvents": True,
        "orderBy": "startTime",
        "maxResults": 50,
    }
    if query:
        params["q"] = query
    events = cal.events().list(**params).execute().get("items", [])
    if not events:
        return "Aucun événement sur cette période."
    lines = []
    for ev in events:
        line = f"id={ev['id']} | {_event_time(ev['start'])} → {_event_time(ev['end'])} | {ev.get('summary', '(sans titre)')}"
        if ev.get("location"):
            line += f" | Lieu : {ev['location']}"
        lines.append(line)
    return "\n".join(lines)


@registry.tool(
    "Crée un événement dans l'agenda Google. Nécessite la confirmation de l'utilisateur.",
    properties={
        "title": {"type": "string"},
        "start": {"type": "string", "description": "Début ISO 8601, ex. 2026-10-02T14:00:00"},
        "end": {"type": "string", "description": "Fin ISO 8601. Défaut : début + 1 h."},
        "description": {"type": "string"},
        "location": {"type": "string"},
        "timezone": {"type": "string", "description": "Fuseau IANA. Défaut Europe/Paris."},
    },
    required=["title", "start"],
    confirm=lambda a: f"créer l'événement « {a.get('title')} » le {a.get('start')}",
)
def calendar_create(
    ctx: ToolContext,
    title: str,
    start: str,
    end: str = "",
    description: str = "",
    location: str = "",
    timezone: str = "Europe/Paris",
) -> str:
    cal = _service(ctx, "calendar", "v3")
    if not end:
        end = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()
    event = {
        "summary": title,
        "description": description,
        "location": location,
        "start": {"dateTime": start, "timeZone": timezone},
        "end": {"dateTime": end, "timeZone": timezone},
    }
    created = cal.events().insert(calendarId="primary", body=event).execute()
    return f"Événement créé : {created.get('htmlLink', created['id'])}"


@registry.tool(
    "Supprime un événement de l'agenda (id via calendar_list). Nécessite la confirmation de l'utilisateur.",
    properties={"event_id": {"type": "string"}},
    required=["event_id"],
    confirm=lambda a: f"supprimer l'événement {a.get('event_id')} de ton agenda",
)
def calendar_delete(ctx: ToolContext, event_id: str) -> str:
    cal = _service(ctx, "calendar", "v3")
    cal.events().delete(calendarId="primary", eventId=event_id).execute()
    return "Événement supprimé."
