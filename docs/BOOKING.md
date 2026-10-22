# Appointments: requests, the clinic's answer, and what is not connected

Audience: the clinic's administrator and engineers. Code: `backend/app/booking/service.py`, `backend/app/routers/booking.py`, migration `backend/migrations/postgres/0010_appointment_requests.sql`;
screens: `frontend/app/book/page.tsx` (patient), `frontend/components/clinic/AppointmentQueue.tsx` and `ClinicProfileForm.tsx` (clinic workspace).

## 1. What this is - and is not

This is an **appointment-request flow**. A patient asks; the clinic's team contacts them and records the day and time they agreed. **No calendar is connected**, so the software never shows free slots, never
reserves a time, and never says an appointment is booked. Every screen and every assistant answer says so. The honest state is also machine-readable: `GET /api/v1/clinic/profile` returns
`booking.mode = "request"` and `availability = { source: "not_connected", slots: [] }`.

## 2. What the clinic must configure (missing configuration, stated precisely)

Nothing is invented: the booking screen and the assistant show only what the clinic has entered. Until then they say "The clinic has not added its contact details here yet. Your request still reaches its team in the app."

| Setting | Where | Effect |
|---|---|---|
| Clinic name, phone, WhatsApp number, e-mail, address, opening hours, a note | Clinic workspace -> "Clinic details and booking" (`PUT /api/v1/clinic/profile`, doctors and administrators) | shown on the booking screen and quoted by the assistant (phone and WhatsApp become tap-to-call / chat links) |
| Online booking page (a plain `https://` address) | same form | if set, the booking screen and the assistant offer "Open the clinic's online booking page" |
| Services and prices | the existing price list ("Approved price list") | only active, approved items are offered in the request form and quoted by the assistant, exactly as entered; the assistant never states a price that is not on the list |
| A live calendar | **not available** | to connect one, `booking.service.public_profile()["availability"]` is the place that would return real slots, and `create_request` would need a reservation step. Until that exists, requests are the only path |

Validation: phone numbers, e-mail and the https address are checked; text fields have length limits; values are stored as entered. Saving records which fields were filled in, never their values, in the audit trail.

## 3. The flow

1. **Patient** opens Book (Home tile, the chat's "Book a dermatologist appointment", or a link from a warning-sign banner) and sends a request: what it is for (3-500 characters), optional service from the price list, preferred days/times, how the clinic should reach them (in the app, phone, e-mail - the number or address is checked), a same-day flag, and - when they came from a conversation with a photo - an **explicit, off-by-default** choice to share a short summary of that photo check with this request.
   Limits: at most 3 open requests per patient; the photo assessment must belong to the patient.
   Consent is required (`consent.patient_consented`). The reply says: **not a confirmed appointment yet**.
2. **Clinic** sees the request in "Appointment requests" (new ones first, same-day first), and every doctor gets a notification. A staff member can mark it *contacted*, **confirm a time**, *decline* or *close* it, with an optional message to the patient. **Confirming needs a typed day and time** ("Tuesday 28 October, 4:30 pm") - the only way a time enters the system is a person writing down what they agreed.
3. **Patient** is notified of every change and sees the status on the Book screen: waiting for the clinic / the clinic has seen your request / confirmed for ... / the clinic could not offer a time / cancelled / closed. An open request can be cancelled by the patient.

| Status | Set by | Meaning |
|---|---|---|
| `requested` | patient | sent, waiting |
| `contacted` | staff | the clinic has seen it |
| `confirmed` | staff | a person recorded an agreed time (`confirmed_for`) |
| `declined`, `closed` | staff | no time offered / finished |
| `cancelled` | patient | withdrawn (only while open) |

### The shared photo summary
If the patient chose to share it, a doctor can open **one** short, factual summary of that photo check (`GET /api/v1/clinic/appointments/{id}/summary`): it is the same text as "Prepare a summary for my dermatologist" and says it is not a diagnosis. The call is audited
(`appointment.summary_view`), fails if nothing was shared or the photo was deleted, and exposes nothing else of the patient's photos or conversations. Clinicians continue to use the existing photo review for the images themselves.

## 4. API

| Method and path | Who | What |
|---|---|---|
| `GET /api/v1/clinic/profile` | any signed-in user | the filled-in details, the services, and the honest availability state |
| `PUT /api/v1/clinic/profile` | doctor, admin | save the clinic's details |
| `POST /api/v1/appointments/requests` | consenting patient | create a request |
| `GET /api/v1/appointments/requests` | patient | their own requests |
| `POST /api/v1/appointments/requests/{id}/cancel` | patient | withdraw an open request |
| `GET /api/v1/clinic/appointments[?status=]` | doctor, admin | the queue |
| `POST /api/v1/clinic/appointments/{id}` | doctor, admin | `status` (contacted/confirmed/declined/closed), `confirmed_for`, `note` |
| `GET /api/v1/clinic/appointments/{id}/summary` | doctor, admin | the photo summary the patient shared |

## 5. In the assistant

"I want to book", "what are your opening hours", "how much does a peel cost", "what is the clinic phone number" are answered by `records_chat.booking_reply` from the configuration above: contact details only if entered,
the approved price list, a link to the request screen (with the photo assessment offered when the conversation has one), the clinic's online page and a call link when set. A quotation request goes to the existing quotation flow.
The assistant never invents a slot, a price or a confirmation.

## 6. Privacy, security, retention

* Rows are patient-owned: `appointment_requests` has row-level security in PostgreSQL (migration `0010`): a patient sees and withdraws only their own; doctors and the server see and answer all; administrators only with the clinical-read flag; a patient **cannot** write a confirmed status or a time for themselves (the update policy only allows `requested`/`cancelled` with no agreed time).
* The audit trail records the request with flags only (urgency, whether a service/photo was attached, contact method), never the reason or contact details.
* Retention category `appointment_requests` (`app/retention.py`) and `POST /api/me/delete-my-data` remove them. The copy script `scripts/sqlite_to_postgres.py` includes the table.
* Notifications to patients contain no medical detail ("Open Book to see the details.").

## 7. Tests

`backend/tests/test_booking.py` (the flow, validation, limits, ownership, the staff rules, the shared summary, retention), `test_pg_rls.py::test_appointment_requests_belong_to_the_patient_and_only_the_clinic_can_agree_a_time` (run on a disposable PostgreSQL 17), and the browser journey `e2e/assistant-guide.mjs` (request, the clinic's queue, confirming with a typed time, the patient seeing it, the shared summary).
Migration `0010` has **not** been applied to the real Supabase project; applying it needs the project owner's go-ahead.
