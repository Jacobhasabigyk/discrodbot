import smtplib
from email.mime.text import MIMEText
import os

def send_verification_email(to_email, code):
    try:
        msg = MIMEText(f"""
Your Buttonland verification code is:

{code}

This expires in 10 minutes.
        """)

        msg["Subject"] = "Your Verification Code"
        msg["From"] = os.getenv("EMAIL_USER")
        msg["To"] = to_email

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(
                os.getenv("EMAIL_USER"),
                os.getenv("EMAIL_PASS")
            )
            server.send_message(msg)

        return True

    except Exception as e:
        print("Email error:", e)
        return False