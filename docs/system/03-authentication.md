# 03 — Authentication & access

*The only thing between the public internet and this account's money.*

**Applies to build:** `0.002` · **Last reviewed:** 2026-09-20

---

## 1. Why the app owns auth

Until the custom domain, IAP fronted the service and the app trusted a header. On
`argentridge.com` the app is its own gate, so `core/auth.py` is written as a security boundary
rather than a convenience.

> **Invariant:** `x-goog-authenticated-user-email` is honoured **only** when
> `VANTAGE_IAP_MODE=true`. If the service were public and that header were trusted, anyone could
> set it and walk in.

## 2. Passwords

**scrypt** from the standard library — memory-hard, no new dependency. `n=2^14, r=8, p=1`, 32-byte
key, 16-byte random salt per user. Comparison is `hmac.compare_digest`, never `==`.

Minimum 12 characters, and a short list of guessable strings is refused.

## 3. Sessions

Opaque 40-byte random tokens. **Only the SHA-256 is stored**, so a Firestore leak does not hand
over live sessions. 14-day expiry, revocable individually or all at once.

> **Invariant:** the cookie holds a token and never user data. Nothing about the account is
> readable from the cookie.

## 4. There is no sign-up

Accounts exist only because the owner created one at `/users`. A new user receives a single-use
setup token and sets their own password, so **the operator never sees, chooses or logs it**.

> **Invariant:** `create_user` is reachable from the owner-gated admin page alone. There is no
> public registration route in the app.

## 5. Brute force

8 failures locks that identity for 15 minutes. A login for an unknown address performs a
comparable amount of hashing work and returns the identical message, so the form cannot be used to
enumerate who has an account.

## 6. Password reset

`/forgot` emails a single-use link valid for 48 hours.

> **Invariant:** the confirmation is byte-identical for a known and an unknown address. Anything
> else turns the form into a user-enumeration oracle.

> **Invariant:** requesting a reset does **not** clear the existing password. Otherwise anyone who
> can trigger one — including an attacker who only knows the address — could lock the owner out.
> The password changes when the new one is set, not when the link is sent.

## 7. Changing your own password

`/profile` requires the **current** password even though the caller is already signed in.

> **Invariant:** a live session is not proof of identity at the keyboard. An unlocked laptop or a
> stolen cookie is exactly the case this defends; without it either becomes permanent takeover.

On success every other session is revoked and the current one is re-minted, so changing a password
evicts every other device without signing you out of the one you are using.

## 8. What a user may not edit about themselves

Name only.

> **Invariant:** role and email are not on the profile form and not accepted by the route. A user
> who could set their own role could promote themselves to owner.

## 9. CSRF

Derived from the session token by HMAC, so it needs no storage. Every state-changing form carries
it and every POST verifies it.
