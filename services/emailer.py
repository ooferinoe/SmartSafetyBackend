from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib
from config import BREVO_USER, BREVO_PASS, BREVO_SENDER
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
    sender_name = "SmartSafety Alerts System"
    sender_email = BREVO_SENDER
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    try:
        with smtplib.SMTP("smtp-relay.brevo.com", 587) as smtp:
            smtp.starttls() 
            smtp.login(BREVO_USER, BREVO_PASS)
            smtp.sendmail(BREVO_SENDER, recipients, msg.as_string())
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
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