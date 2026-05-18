## HOW TO MAKE RECOMMENDATIONS

- Reference actual Django apps, file paths, or Docker service names
  (e.g. 'check inbox/tasks.py', 'restart celery-worker', 'check nginx rate limiting')
- Do NOT suggest load balancers, Kubernetes, or CDN — single-server personal project
- Do NOT recommend adding monitoring tools — Sentry is already integrated
- For attacks: suggest concrete Nginx/Django countermeasures (rate limiting, IP blocking,
  fail2ban config) appropriate for a DigitalOcean single-droplet setup
