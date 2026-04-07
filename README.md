# TJ RMM (Remote Monitoring and Management)

A Django-based RMM solution for managing client machines, handling support requests, and processing subscriptions via Stripe.

## Features

- **Technician Dashboard**: Monitor machines, alerts, and support requests.
- **Client Portal**: Secure access for clients to view machines, submit requests, and manage billing.
- **Subscription Management**: Integrated with Stripe for billing and plan management.
- **Real-time Monitoring**: Track machine health, processes, and alerts.
- **Multi-tenant**: Separate client access with role-based permissions.

## Quick Demo

After setup, load demo data:

```bash
python manage.py shell -c "
from agents.models import Client, ClientAccess, SubscriptionPlan, ClientSubscription, Machine
from django.contrib.auth.models import User
from django.utils import timezone

plan, _ = SubscriptionPlan.objects.get_or_create(name='Demo Plan', defaults={'description': 'Demo subscription plan', 'monthly_price_cents': 9900, 'max_machines': 10})
client, _ = Client.objects.get_or_create(name='Demo Company', defaults={'contact_email': 'demo@company.com'})
user, _ = User.objects.get_or_create(username='demo', defaults={'email': 'demo@company.com', 'first_name': 'Demo', 'last_name': 'User'})
user.set_password('Demo123!')
user.save()
access, _ = ClientAccess.objects.get_or_create(user=user, client=client, defaults={'role': ClientAccess.ROLE_OWNER})
subscription, _ = ClientSubscription.objects.get_or_create(client=client, plan=plan, defaults={'status': ClientSubscription.STATUS_ACTIVE, 'billing_email': 'demo@company.com', 'start_date': timezone.now().date(), 'current_period_end': timezone.now().date() + timezone.timedelta(days=30)})
machine, _ = Machine.objects.get_or_create(client=client, hostname='DEMO-PC-01', defaults={'os_info': 'Windows 11 Pro', 'ip_address': '192.168.1.100', 'last_seen': timezone.now(), 'cpu_model': 'Intel Core i7', 'ram_gb': 16, 'disk_usage_percent': 45})
print('Demo data loaded!')
"
```

**Demo Login:**
- Client Portal: `demo` / `Demo123!`
- Admin: Create with `python manage.py createsuperuser`

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
- `TJ_RMM_AGENT_EXE_PATH`: Absolute path to downloadable Windows installer EXE (default: `media/deployments/tj-rmm-agent.exe`)

### 30-Day Trial Onboarding

- Public signup: `/client/signup/`
- Sales CTA from landing page routes directly to trial signup.
- Trial account provisions automatically:
  - Client organization
  - Owner user
  - Client access mapping
  - Trial subscription valid for 30 days
- After trial expiry, client portal access is blocked until billing is activated.

### Agent EXE Build and Publish

1. Build a Windows x64 executable:
   ```powershell
   cd ..\TJ_RMM_Agent
   powershell -ExecutionPolicy Bypass -File .\build-agent-exe.ps1
   ```
2. Copy built file to server path:
   - Source: `TJ_RMM_Agent\tj-rmm-agent.exe`
   - Target: `TJ_RMM_Server\media\deployments\tj-rmm-agent.exe`
3. Clients can download from the portal button:
   - `/client/agent/download/`
4. Technician one-click publish page:
   - `/operations/agent-installer/`

To avoid the Windows "This app can't run on your PC" error, make sure you build the EXE on Windows for x64 using the provided build script.

Agent runtime configuration (set on target machines before service startup):

- `TJ_RMM_SERVER_URL`: Example `https://yourdomain.com/api/hub/`
- `TJ_RMM_API_KEY`: Must match server `AGENT_KEY`
- `TJ_RMM_CLIENT_NAME`: Client/company name to associate check-ins

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