# Snappic AI Alt-Text — Accessible Photo Booth Descriptions

This service makes photo-booth photos accessible to blind and low-vision guests.
When a guest takes a picture, it uses Claude's vision AI to write a short
description of the photo, and texts that description to the guest's phone — so
their phone's screen reader (VoiceOver on iPhone, TalkBack on Android) can read
aloud what their photo shows.

It works alongside Snappic without changing anything about how the booth runs.
Guests still get their photo exactly as before; they just also get a second text
describing it.

---

## How it works

```
  Guest taps the button and takes a photo
                │
                ▼
  Snappic captures it and (1) fires a "session" webhook to this service
                │                         │
                │                         ▼
                │              This service downloads the photo,
                │              asks Claude to describe it, and
                │              stores the description.  (~2–5 seconds)
                │
  Guest enters their phone number to receive the photo
                │
                ▼
  Snappic sends the photo AND (2) fires a "share" webhook to this service
                                          │
                                          ▼
                              This service texts the stored
                              description to the guest's number.
                                          │
                                          ▼
              Guest's phone reads the description aloud. ✅
```

Because the description is written the moment the photo is captured, it's
already waiting by the time the guest shares — so the accessible text arrives
right after their photo. If the two events happen out of order, the service
handles it and still delivers exactly one text.

---

## What you'll need (about 30–45 minutes to set up)

1. **A Snappic account with webhooks** — confirm your plan includes webhook /
   "Webhooks Overview" access. You'll point Snappic at this service's two URLs.
2. **An Anthropic (Claude) API key** — from <https://console.anthropic.com>.
   This powers the descriptions. (Roughly a fraction of a cent per photo — see
   "Costs" below.)
3. **A Twilio account and a phone number** — from <https://console.twilio.com>.
   This sends the text messages.
4. **Somewhere to run the service** — any host that runs a Python web app. This
   guide uses **Render** as the easy default, and includes Docker for anything
   else.

You do **not** need to be a developer to follow this, but you will be
copying keys between websites and running a couple of commands. If you have a
developer, hand them this README and they'll have it up quickly.

---

## Table of contents

1. [Get the code running locally](#1-get-the-code-running-locally)
2. [Get your API keys](#2-get-your-api-keys)
3. [Configure the service (`.env`)](#3-configure-the-service-env)
4. [Test it locally](#4-test-it-locally)
5. [Deploy it to the internet](#5-deploy-it-to-the-internet)
6. [Connect Snappic's webhooks](#6-connect-snappics-webhooks)
7. [Confirm the payload shape](#7-confirm-the-payload-shape-important)
8. [Do a real end-to-end test](#8-do-a-real-end-to-end-test)
9. [Going-live checklist](#9-going-live-checklist)
10. [Accessibility & consent notes](#10-accessibility--consent-notes)
11. [Costs](#11-costs)
12. [Scaling for big events](#12-scaling-for-big-events)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Get the code running locally

You need **Python 3.11+**. Check with `python3 --version`.

```bash
cd snappic-alt-text
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Run the tests to confirm everything's healthy (these use fakes — no keys needed):

```bash
python -m pytest -q
```

You should see `15 passed`.

---

## 2. Get your API keys

### Anthropic (Claude)

1. Go to <https://console.anthropic.com> → **API Keys** → **Create Key**.
2. Copy the key (starts with `sk-ant-`).
3. Add a little credit under **Billing** so it can make calls.
4. Confirm the current vision model name at
   <https://docs.claude.com/en/docs/about-claude/models> — you'll put it in
   `ANTHROPIC_MODEL`. Any current Claude model that accepts images works.

### Twilio (SMS)

1. Go to <https://console.twilio.com> and sign up / log in.
2. From the dashboard copy your **Account SID** and **Auth Token**.
3. Buy a phone number with SMS capability: **Phone Numbers → Buy a number**
   (make sure "SMS" is checked). This is the number your descriptions send from.
4. **US A2P 10DLC note:** to text US numbers reliably at any volume, Twilio
   requires you to register your business/brand and a campaign. Start this
   early — approval can take a few days. Twilio walks you through it under
   **Messaging → Regulatory Compliance**. (For a quick test you can text your
   own verified number first.)

---

## 3. Configure the service (`.env`)

Copy the template and fill it in:

```bash
cp .env.example .env
```

Open `.env` and set:

- `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL`
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`
- `SNAPPIC_WEBHOOK_SECRET` — invent a long random string; you'll paste the same
  value into Snappic later.
- `SNAPPIC_VERIFY_MODE` — leave as `token` for now (see Section 6).
- `SMS_PREFIX` — the words before the description, e.g. `Photo description:`
- `EVENT_NAME` — optional, appended in parentheses.
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` — the login for the staff dashboard
  (Section 8.5). Set a password to enable the dashboard.

Every setting is documented inline in `.env.example`.

---

## 4. Test it locally

Start the server:

```bash
./run.sh          # or: uvicorn app.main:app --reload
```

Visit <http://localhost:8000/> — you should see a JSON status page confirming
which pieces are configured.

Now simulate the two webhooks with `curl`. (Set `SNAPPIC_VERIFY_MODE=none` in
`.env` while testing locally so you don't need a signature, then restart.)

**Simulate a capture** (use any public image URL as the "photo"):

```bash
curl -X POST http://localhost:8000/webhooks/snappic/session \
  -H "Content-Type: application/json" \
  -d '{"session": {"id": "test-1", "type": "photo",
       "direct_url": "https://raw.githubusercontent.com/EbookFoundation/free-programming-books/main/images/free-programming-books-lg.png"}}'
```

Check the description was generated:

```bash
curl http://localhost:8000/sessions/test-1
```

You should see `"caption_status": "ready"` and an `alt_text` value.

**Simulate the share** (texts your own phone — use a number verified in Twilio):

```bash
curl -X POST http://localhost:8000/webhooks/snappic/share \
  -H "Content-Type: application/json" \
  -d '{"session": {"id": "test-1"}, "method": "sms", "recipient": "+1YOURNUMBER"}'
```

Your phone should receive the description text. That's the whole product working.

---

## 5. Deploy it to the internet

Snappic needs to reach your service over HTTPS, so it has to be hosted (not just
on your laptop). Any of these work; **Render** is the simplest.

### Option A — Render (recommended, no Docker knowledge needed)

1. Push this folder to a GitHub repo (or use Render's "deploy from folder").
2. On <https://render.com> → **New → Web Service** → connect the repo.
3. Settings:
   - **Runtime:** Python
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Add all your `.env` values under **Environment → Environment Variables**
   (don't upload the `.env` file itself). Set `SNAPPIC_VERIFY_MODE=token`.
5. Deploy. Render gives you a public URL like
   `https://your-app.onrender.com`. That's your base URL.
6. **Video support:** if you want descriptions for video captures, add `ffmpeg`.
   The easiest way is to deploy with the included **Dockerfile** instead
   (Render supports Docker: choose "Docker" as the runtime). Photos and GIFs
   don't need ffmpeg.

> Note: free Render instances sleep when idle and take a few seconds to wake.
> For a live event use a paid instance so the first guest isn't kept waiting.

### Option B — Docker (any host: Fly.io, Railway, a VPS, etc.)

```bash
docker build -t snappic-alt-text .
docker run -p 8000:8000 --env-file .env snappic-alt-text
```

The Docker image already includes `ffmpeg`, so video works out of the box. Put
it behind HTTPS (your platform usually provides this automatically).

Whatever you choose, confirm `https://YOUR_URL/healthz` returns
`{"status":"ok"}` before moving on.

---

## 6. Connect Snappic's webhooks

In Snappic (see their **Webhooks Overview** help article for the exact screen),
add two webhook subscriptions pointing at your deployed URL:

| Snappic event            | Point it to                                   |
|--------------------------|-----------------------------------------------|
| Session / media captured | `https://YOUR_URL/webhooks/snappic/session`   |
| Share sent (SMS/email)   | `https://YOUR_URL/webhooks/snappic/share`     |

**Securing the webhooks.** Set the shared secret so only Snappic can trigger
your service:

- If Snappic lets you add a **custom header** to its webhooks, add a header named
  `X-Snappic-Signature` (or whatever you set in `SNAPPIC_SIGNATURE_HEADER`) with
  the value equal to your `SNAPPIC_WEBHOOK_SECRET`. Keep `SNAPPIC_VERIFY_MODE=token`.
- If Snappic **signs** its payloads with a secret (HMAC), set
  `SNAPPIC_VERIFY_MODE=hmac` and put Snappic's signing secret in
  `SNAPPIC_WEBHOOK_SECRET`. The code checks common signature formats; if
  Snappic's differs, `app/security.py` is small and easy to adjust.
- If Snappic offers neither yet, you can run `SNAPPIC_VERIFY_MODE=none`
  temporarily, but add security before a real event.

---

## 7. Confirm the payload shape (important!)

Snappic doesn't publish the exact JSON field names in its webhooks, so the code
reads them defensively (it checks several likely names). To be certain it's
reading the right fields, capture one real payload and check:

1. Temporarily set `LOG_LEVEL=DEBUG` and redeploy.
2. Take one photo at the booth and share it to yourself.
3. Look at your service logs for `Webhook payload:` — that's exactly what Snappic
   sent.
4. Compare the real field names to the lists in **`app/schemas.py`**
   (`_CAPTURE_FIELDS` and `_SHARE_FIELDS`). If Snappic uses a name that isn't
   already listed, add it — this is the *one* place you'd ever need to edit, and
   it's just adding a string to a list.
5. Set `LOG_LEVEL=INFO` again when done (so you're not logging guest phone
   numbers routinely).

Everything downstream (captioning, delivery) is unaffected by field-name
differences once `schemas.py` maps them.

---

## 8. Do a real end-to-end test

At an actual booth (or a Snappic test event):

1. Take a photo.
2. Within a few seconds, check `https://YOUR_URL/sessions` — your session should
   show `caption_status: ready` with a sensible `alt_text`.
3. Share the photo to your own phone by SMS.
4. Confirm you get **two** messages: Snappic's photo, and the description.
5. Turn on VoiceOver/TalkBack and confirm the description reads aloud clearly.

Read a few of the generated descriptions to judge quality, and tune the wording
in `app/captioner.py` (the `_SYSTEM` prompt) if you want a different style —
shorter, warmer, more/less detail.

---

## 8.5 The staff dashboard

There's a live dashboard for staff to watch descriptions come in during an event
and to fix any that need help. Open **`https://YOUR_URL/admin`** in a browser and
log in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

It shows every capture as it happens, auto-refreshing every few seconds:

- **Live status** for each photo — whether it's been described yet, and whether
  the text was delivered (with a count if it was re-sent).
- **Summary counts** at the top: captured, described, delivered, and how many
  need attention.
- **A "Needs attention" filter** that surfaces only the problems — a description
  that failed, a text that didn't send, or a guest with no number on file.
- **Edit-and-resend.** Every row has the description in an editable box. If the AI
  got something wrong, a staff member types a correction and clicks **Resend
  correction** — the guest immediately gets a new text with the fixed wording.
  For a photo whose text failed or hasn't been sent, the same button reads
  **Send description** and sends it for the first time.

This is your safety net: if a description isn't quite right, a person can fix it
and re-send in seconds, right from their phone or laptop.

> Security: the dashboard and all guest-data endpoints require the admin login.
> Guest phone numbers are masked in the display (full number on hover). Use a
> strong `ADMIN_PASSWORD`, and always run behind HTTPS (your host provides this).

Endpoints, for reference:

| Method & path                               | What it does                        |
|---------------------------------------------|-------------------------------------|
| `GET /admin`                                | The dashboard page                  |
| `GET /admin/api/sessions`                   | JSON feed the dashboard polls       |
| `POST /admin/api/sessions/{id}/resend`      | Correct + (re)send a description     |

---

## 9. Going-live checklist

- [ ] `https://YOUR_URL/healthz` returns ok, and `/` shows both
      `anthropic_configured` and `twilio_configured` as `true`.
- [ ] Webhook verification is **on** (`token` or `hmac`, not `none`).
- [ ] Twilio A2P 10DLC registration approved (for US SMS at volume).
- [ ] Payload field names confirmed against a real Snappic payload (Section 7).
- [ ] Paid/always-on hosting so there's no cold-start delay for the first guest.
- [ ] You've read ~5 sample descriptions and are happy with the tone.
- [ ] `ADMIN_PASSWORD` set to something strong, and staff know the `/admin` URL.
- [ ] Guest signage/consent in place (see below).

---

## 10. Accessibility & consent notes

- **This is an assist, not a guarantee.** Vision AI is good but not perfect. For
  a high-stakes event, consider having a staff member able to spot-review or
  re-send a corrected description. The prompt already tells Claude not to guess
  names, ages, or identities.
- **Consent / opt-in.** Guests already give Snappic their number to receive the
  photo, but they're getting a *second* message from *you*. Put up clear signage
  ("Photos come with an accessible text description") and make sure your setup
  complies with SMS rules (opt-in, and an opt-out path — Twilio auto-handles
  STOP/HELP on registered numbers).
- **Message wording.** Keep it clean for screen readers: no emojis, lead with the
  description. `SMS_PREFIX` and `EVENT_NAME` control the framing.
- **Privacy.** Descriptions and phone numbers are stored in the database so the
  two events can be matched. Treat that database as sensitive, and consider
  clearing it after each event. Avoid running `LOG_LEVEL=DEBUG` during a live
  event since payloads contain phone numbers.

---

## 11. Costs

Rough, per photo:

- **Claude description:** typically well under a cent per image (a single small
  image + ~150 output tokens). Check current pricing at
  <https://www.anthropic.com/pricing>.
- **Twilio SMS:** around a cent or so per US text, plus a small monthly number
  fee. See <https://www.twilio.com/en-us/sms/pricing/us>.
- **Hosting:** a small always-on instance is usually a few dollars a month.

So a 200-photo event is a couple of dollars in AI + SMS. Confirm live pricing on
those pages before budgeting.

---

## 12. Scaling for big events

The service processes captures in the background and can handle a normal booth's
pace comfortably. For very high volume (many booths, rapid-fire captures):

- It currently uses FastAPI background tasks + a database — simple and reliable
  for typical events. For thousands of near-simultaneous captures, move the
  processing to a real job queue (Celery/RQ + Redis, or a cloud queue) — the
  `handle_capture` / `handle_share` functions in `app/processing.py` are written
  so they can be moved onto a worker with minimal change.
- Switch `DATABASE_URL` from SQLite to Postgres for multi-instance deployments
  (SQLite is fine for a single instance).
- Run more than one web instance behind the host's load balancer; delivery is
  idempotent (guests never get a double text), so this is safe.

---

## 13. Troubleshooting

**No description is generated (`caption_status: failed`).**
Check `caption_error` on the session (`GET /sessions/{id}`). Common causes: wrong
`ANTHROPIC_MODEL`, no Anthropic credit, or the media URL wasn't reachable.

**No text arrives.**
Check `delivery_status` / `delivery_error`. Common causes: Twilio number can't
send to that region, A2P registration incomplete, or the phone field wasn't
parsed — confirm the payload shape (Section 7).

**Webhook returns 401.**
Signature/token mismatch. Make sure the secret in Snappic exactly matches
`SNAPPIC_WEBHOOK_SECRET`, and the header name matches `SNAPPIC_SIGNATURE_HEADER`.

**Video captures fail to describe.**
`ffmpeg` isn't installed. Deploy via the Dockerfile (it includes ffmpeg), or
install ffmpeg on your host. Photos and GIFs don't need it.

**Descriptions arrive but are low quality.**
Tune the `_SYSTEM` prompt in `app/captioner.py`, or set a stronger
`ANTHROPIC_MODEL`.

---

## Project layout

```
snappic-alt-text/
├── app/
│   ├── main.py         # FastAPI app + the two webhook routes + status API
│   ├── config.py       # settings loaded from .env
│   ├── schemas.py      # ← maps Snappic's payload fields (edit here if needed)
│   ├── security.py     # webhook signature/token verification
│   ├── media.py        # download + reduce photo/gif/video to one still image
│   ├── captioner.py    # Claude vision → alt-text (edit the prompt here)
│   ├── sms.py          # Twilio delivery + message wording
│   ├── processing.py   # the orchestration (capture → caption → deliver → resend)
│   ├── dashboard.py    # the staff admin dashboard (single HTML page)
│   ├── models.py       # database model
│   └── db.py           # database setup
├── tests/              # full test suite (mocked; run with pytest)
├── requirements.txt
├── .env.example        # copy to .env and fill in
├── Dockerfile          # includes ffmpeg for video support
├── Procfile
└── run.sh              # local dev server
```

The two files you're most likely to touch are **`app/schemas.py`** (if Snappic's
field names differ from the defaults) and **`app/captioner.py`** (to adjust the
description style).
