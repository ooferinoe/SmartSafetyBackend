import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import GMAIL_USER, GMAIL_PASS

def send_alert(to_emails, violation_type, confidence, date_time):
    if isinstance(to_emails, list):
        to = to_emails[0]
    else:
        to = to_emails

    subject = f"Violation Alert: {violation_type}"
    body = f"Violation: {violation_type}\nConfidence: {confidence}\nDate & Time: {date_time}"

    msg = MIMEMultipart()
    msg['From'] = GMAIL_USER
    msg['To'] = to
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_PASS)
        smtp.send_message(msg)