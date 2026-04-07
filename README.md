# TJ RMM (Remote Monitoring and Management)

A Django-based RMM solution for managing client machines, handling support requests, and processing subscriptions via Stripe.

## Features

- **Technician Dashboard**: Monitor machines, alerts, and support requests.
- **Client Portal**: Secure access for clients to view machines, submit requests, and manage billing.
- **Subscription Management**: Integrated with Stripe for billing and plan management.
- **Real-time Monitoring**: Track machine health, processes, and alerts.
- **Multi-tenant**: Separate client access with role-based permissions.

## Quick Start

### Prerequisites

- Python 3.13+
- SQLite (default) or PostgreSQL/MySQL

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/tj-rmm.git
   cd tj-rmm
   ```

2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Run migrations:
   ```bash
   python manage.py migrate
   ```

5. Create a superuser:
   ```bash
   python manage.py createsuperuser
   ```

6. Run the server:
   ```bash
   python manage.py runserver
   ```

7. Access the app at `http://127.0.0.1:8000/`

### Configuration

Set environment variables for production:

- `TJ_RMM_SECRET_KEY`: Django secret key
- `TJ_RMM_DEBUG`: Set to `false` for production
- `TJ_RMM_DATABASE_URL`: Database URL (e.g., PostgreSQL)
- `TJ_RMM_STRIPE_SECRET_KEY`: Stripe secret key
- `TJ_RMM_STRIPE_PUBLISHABLE_KEY`: Stripe publishable key
- `TJ_RMM_STRIPE_WEBHOOK_SECRET`: Stripe webhook secret
- `TJ_RMM_EMAIL_BACKEND`: Email backend (default: console)
- `TJ_RMM_DEFAULT_FROM_EMAIL`: From email address

### Stripe Setup

1. Create a Stripe account and get API keys.
2. Set webhook endpoint to `https://yourdomain.com/stripe/webhook/`
3. Configure environment variables as above.

### Deployment

Use Docker or deploy to a cloud provider like Heroku, AWS, or Azure.

Example Docker setup:

```dockerfile
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN python manage.py collectstatic --noinput

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
```

## Usage

- **Admin**: Access `/admin/` for managing clients, plans, etc.
- **Technician**: Login at `/` for dashboard.
- **Client**: Invite clients via admin, they access `/client/`.

## Contributing

1. Fork the repo.
2. Create a feature branch.
3. Make changes and add tests.
4. Submit a PR.

## License

MIT License