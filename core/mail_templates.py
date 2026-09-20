"""Transactional emails (password setup/reset).

Plain, table-free, no images — these must render anywhere and must not look
like phishing. Every one states what to do if the recipient did NOT ask for it.
"""
from __future__ import annotations

BRAND = "Argent Ridge"


def _wrap(title: str, body: str) -> str:
    return f'''<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
 max-width:520px;margin:0 auto;padding:28px 24px;color:#26313d;">
  <div style="font-size:15px;font-weight:700;letter-spacing:2px;color:#3e7c8f;
   margin-bottom:22px;">{BRAND.upper()}</div>
  <div style="font-size:17px;font-weight:600;margin-bottom:12px;">{title}</div>
  {body}
  <hr style="border:none;border-top:1px solid #e6eaef;margin:26px 0 12px;">
  <div style="font-size:11.5px;color:#93a1b0;">
    {BRAND} is a private staff tool. If you did not expect this email, you can
    ignore it &mdash; the link does nothing until it is opened, and it expires on its own.
  </div>
</div>'''


def _button(url: str, label: str) -> str:
    return (f'<p style="margin:22px 0;"><a href="{url}" '
            f'style="background:#3e7c8f;color:#ffffff;text-decoration:none;'
            f'padding:11px 20px;border-radius:9px;font-size:14px;font-weight:600;'
            f'display:inline-block;">{label}</a></p>'
            f'<p style="font-size:12px;color:#6e7c8a;word-break:break-all;">'
            f'Or paste this into your browser:<br>{url}</p>')


def invite(name: str, url: str, hours: int) -> tuple[str, str]:
    body = (f'<p style="font-size:14px;line-height:1.6;">Hello {name}, an account has been '
            f'created for you on {BRAND}. Choose a password to finish setting it up.</p>'
            + _button(url, "Set your password")
            + f'<p style="font-size:12.5px;color:#6e7c8a;">This link works once and '
              f'expires in {hours} hours. Passwords must be at least 12 characters.</p>')
    return f"Set up your {BRAND} account", _wrap(f"Welcome to {BRAND}", body)


def reset(name: str, url: str, hours: int) -> tuple[str, str]:
    body = (f'<p style="font-size:14px;line-height:1.6;">Hello {name}, we received a request '
            f'to reset the password for your {BRAND} account.</p>'
            + _button(url, "Choose a new password")
            + f'<p style="font-size:12.5px;color:#6e7c8a;">This link works once and expires '
              f'in {hours} hours. Your current password stays active until you set a new one.'
              f'</p>'
              f'<p style="font-size:12.5px;color:#6e7c8a;"><b>If this was not you</b>, no '
              f'action is needed &mdash; nothing changes unless the link is used.</p>')
    return f"Reset your {BRAND} password", _wrap("Password reset", body)
