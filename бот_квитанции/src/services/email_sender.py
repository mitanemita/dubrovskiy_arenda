import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from src.utils.logger import logger
from src.utils.config import EMAIL_CONFIG

async def send_email(subject: str, body: str, to_email: str, file_data: bytes = None, filename: str = "document.pdf"):
    logger.info(f"Начало отправки email на {to_email}")
    
    msg = MIMEMultipart()
    msg['From'] = EMAIL_CONFIG["email"]
    msg['To'] = to_email
    msg['Subject'] = subject

    msg.attach(MIMEText(body, 'plain'))

    if file_data:
        part = MIMEApplication(file_data, _subtype="pdf")
        part.add_header('Content-Disposition', 'attachment', filename=filename)
        msg.attach(part)

    try:
        server = smtplib.SMTP_SSL(EMAIL_CONFIG["smtp_server"], EMAIL_CONFIG["smtp_port"])
        server.login(EMAIL_CONFIG["email"], EMAIL_CONFIG["password"])
        server.send_message(msg)
        server.quit()
        logger.info(f"Email успешно отправлен на {to_email}")
        return True
    except Exception as e:
        logger.exception(f"Ошибка при отправке email на {to_email}: {e}")
        return False



