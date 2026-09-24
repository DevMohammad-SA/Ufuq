# Security Policy

## Supported Versions

Only the latest release receives security fixes.

| Version | Supported |
|---------|-----------|
| 1.2.x   | ✅        |
| < 1.2   | ❌        |

## Reporting a Vulnerability

**Please do not open a public issue for security vulnerabilities.**

This platform is used in production and stores personal data of minors
(national IDs and phone numbers). Report vulnerabilities privately via
GitHub's **Private vulnerability reporting**:
the repository's **Security** tab → **Report a vulnerability**.

Please include:
- A description of the issue and its potential impact
- Steps to reproduce
- Affected page, endpoint, or file (if known)

## Scope and Rules

- **Do not test against the production site** (`rahhal.saqeel.org.sa`).
  Run the project locally (see README) to reproduce issues.
- Do not access, modify, or download any real participant data.
- Do not perform denial-of-service or automated scanning against production.

## Response

This project is maintained by a single developer. You can expect an
initial response within 7 days. Please allow reasonable time for a fix
before any public disclosure.

---

## الإبلاغ عن ثغرة أمنية

**لا تنشر الثغرات في Issue عام.** بلّغ بشكل خاص عبر تبويب **Security**
في المستودع ← **Report a vulnerability**. لا تختبر على الموقع الفعلي،
ولا تصل لأي بيانات حقيقية للمشاركين.
