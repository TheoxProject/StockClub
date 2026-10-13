# Security policy

## Reporting a concern

Please do not post passwords, connection strings, API keys, private photos, or other sensitive data in a public issue. If you discover a potential security issue, contact the repository maintainer privately through GitHub before disclosing technical details publicly.

## Deployment basics

- Keep all real credentials in environment variables or the hosting provider's secret store.
- Keep the photo bucket private unless public access has been deliberately reviewed.
- Use HTTPS, strong unique passwords, and supported dependency versions.
- Back up PostgreSQL and object storage separately, and test restoration.
