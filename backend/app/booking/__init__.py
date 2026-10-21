"""Appointment requests and the clinic's public profile.

The product has no calendar or live availability, and this package does not pretend otherwise. What it does:
* the clinic describes itself once (name, phone, e-mail, address, hours, an optional online booking page) in the settings table; nothing is shown that the clinic did not enter;
* a patient sends an appointment REQUEST (reason, preferred times, how to be contacted); the clinic's doctors are notified; the request is never a booking;
* a member of the clinic's staff contacts the patient and may record the time they agreed, in their own words; only then does the patient see 'confirmed', with that text.
Real availability needs a calendar or booking system: docs/BOOKING.md says what to connect."""
