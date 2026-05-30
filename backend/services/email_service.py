import smtplib
from email.message import EmailMessage


def build_email_message(config, subject, body, recipients, cc=None, bcc=None):
    message = EmailMessage()
    sender_name = (config.get('email_sender_name') or '').strip()
    sender_email = (config.get('email_sender_email') or config.get('email_from') or '').strip()
    from_value = f"{sender_name} <{sender_email}>" if sender_name else sender_email
    message['From'] = from_value
    message['To'] = ', '.join(recipients)
    if cc:
        message['Cc'] = ', '.join(cc)
    message['Subject'] = subject
    message.set_content(body)
    return message


def send_email_via_smtp(config, subject, body, recipients, cc=None, bcc=None):
    host = (config.get('email_smtp_host') or '').strip()
    port = int(config.get('email_smtp_port') or 587)
    username = (config.get('email_smtp_username') or '').strip()
    password = (config.get('email_smtp_password') or '').strip()
    sender = (config.get('email_sender_email') or config.get('email_from') or '').strip()
    use_tls = str(config.get('email_use_tls', 'true')).lower() == 'true'

    if not host or not port or not sender:
        raise ValueError('SMTP host, port, and sender email are required')
    if not recipients:
        raise ValueError('At least one recipient is required')

    cc = cc or []
    bcc = bcc or []
    msg = build_email_message(config, subject, body, recipients, cc=cc, bcc=bcc)
    with smtplib.SMTP(host, port, timeout=20) as server:
        server.ehlo()
        if use_tls:
            server.starttls()
            server.ehlo()
        if username and password:
            server.login(username, password)
        server.send_message(msg, to_addrs=recipients + cc + bcc)

    return {
        'subject': subject,
        'recipients': recipients,
        'cc': cc,
        'bcc': bcc,
        'from': sender,
    }
