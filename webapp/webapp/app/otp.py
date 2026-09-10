import os
import smtplib
from email.mime.text import MIMEText

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = os.getenv("SMTP_PORT")
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")


def send_otp_email(to_email: str, otp: str, purpose: str) -> None:
    subject = "Your verification code" if purpose == "signup" else "Your password reset code"
    body = f"Your OTP is {otp}. It expires in 10 minutes."

    if not SMTP_HOST:
        # Dev fallback — no SMTP configured, print instead of sending
        print(f"[DEV OTP] To: {to_email} | Purpose: {purpose} | OTP: {otp}")
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = to_email

    with smtplib.SMTP(SMTP_HOST, int(SMTP_PORT)) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, [to_email], msg.as_string())
