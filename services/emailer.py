from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib
from config import GMAIL_USER, GMAIL_PASS
from firebase_admin import firestore

def _normalize_recipients(to_emails):
    if isinstance(to_emails, str):
        return [to_emails]
    if isinstance(to_emails, (list, tuple)):
        return to_emails
    return []

def send_alert(to_emails, violation_type, confidence, date_time):
    recipients = _normalize_recipients(to_emails)
    if not recipients:
        return False
    subject = f"Violation Alert: {violation_type}"
    body = f"Violation: {violation_type}\nConfidence: {confidence}\nWhen: {date_time}\n\nThis is an automated alert."
    msg = MIMEMultipart()
    msg["From"] = GMAIL_USER
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_PASS)
            smtp.sendmail(GMAIL_USER, recipients, msg.as_string())
        return True
    except Exception:
        return False

def send_alert_to_uid(uid, violation_type, confidence, date_time):
    db = firestore.client()
    doc = db.collection("users").document(uid).get()
    if not doc.exists:
        return False
    email = (doc.to_dict() or {}).get("email")
    if not email:
        return False
    return send_alert(email, violation_type, confidence, date_time)