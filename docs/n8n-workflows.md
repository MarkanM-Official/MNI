# n8n Workflow Ideas

MNI uses a bring-your-own n8n setup. Add your n8n URL, API key, default webhook URL, and trigger keyword in the admin `Automations` panel.

## Lead Capture

Trigger: `/workflow lead name email message`

Suggested n8n nodes:

- Webhook
- Google Sheets append row
- Gmail or SMTP welcome email
- Slack or Discord notification
- Respond to Webhook with `{ "reply": "Lead saved." }`

## Book Meeting

Trigger: `/workflow book meeting tomorrow 4pm`

Suggested n8n nodes:

- Webhook
- Google Calendar availability check
- Google Meet create link
- Respond to Webhook with `{ "reply": "Meeting booked: <link>" }`

## Support Ticket

Trigger: `/workflow support issue details`

Suggested n8n nodes:

- Webhook
- Notion, Airtable, HubSpot, or GitHub issue node
- Email acknowledgement
- Respond to Webhook

The Telegram bot sends message metadata such as `chat_id`, `chat_type`, `user_id`, `username`, `trigger`, and `message`.
