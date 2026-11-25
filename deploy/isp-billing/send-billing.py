#!/usr/bin/python3
"""Send one monthly retro ISP statement per configured mailbox."""
import argparse
import calendar
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate
import fcntl
import hashlib
import json
from pathlib import Path
import smtplib
import tempfile
import os


def statement(config, recipient, now):
    period = now.strftime('%B %Y')
    message = EmailMessage()
    message['From'] = config['sender']
    message['To'] = recipient
    message['Date'] = formatdate(localtime=True)
    message['Subject'] = config['isp_name']+' - monthly statement - '+period
    key = hashlib.sha256((recipient+now.strftime('%Y-%m')).encode()).hexdigest()[:24]
    message['Message-ID'] = '<retro-billing-'+key+'@net.cryan.nz>'
    message.set_content(
        config['isp_name']+'\nMonthly account statement\n\n'
        'Statement period: '+period+'\n'
        'Mailbox: '+recipient+'\n'
        'Plan: '+config['plan_name']+'\n'
        'Monthly charge: '+config['currency']+' '+config['monthly_amount']+'\n\n'
        'This is a retro ISP simulation. No payment is due or collected.\n'
        'Thank you for connecting!\n', charset='us-ascii')
    return message


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default='/etc/retro-isp-billing.json')
    ap.add_argument('--state', default='/var/lib/retro-isp-billing')
    ap.add_argument('--preview', action='store_true')
    args = ap.parse_args()
    config = json.loads(Path(args.config).read_text())
    recipients = config.get('recipients', [])
    if not recipients:
        raise SystemExit('No billing recipients configured; nothing sent')
    for recipient in recipients:
        if '\n' in recipient or '\r' in recipient or not recipient.endswith('@net.cryan.nz'):
            raise SystemExit('Billing recipients must be local net.cryan.nz mailboxes')
    now = datetime.now()
    if args.preview:
        for recipient in recipients:
            print(statement(config, recipient, now).as_string())
        return
    state = Path(args.state)
    state.mkdir(parents=True, exist_ok=True)
    with (state/'send.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = state/'sent.json'
        sent = json.loads(ledger.read_text()) if ledger.exists() else {}
        for recipient in recipients:
            key = now.strftime('%Y-%m')+':'+recipient
            if key in sent:
                continue
            message = statement(config, recipient, now)
            with smtplib.SMTP(config['smtp_host'], config.get('smtp_port', 25), timeout=20) as smtp:
                smtp.send_message(message)
            sent[key] = message['Message-ID']
            fd, name = tempfile.mkstemp(dir=state, prefix='sent-', suffix='.tmp')
            try:
                with os.fdopen(fd, 'w') as out:
                    json.dump(sent, out)
                os.replace(name, ledger)
            finally:
                Path(name).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
