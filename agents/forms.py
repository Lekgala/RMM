from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import AuthenticationForm

from .models import Client, ClientAccess, ClientInvitation, ServiceRequest, ServiceRequestNote, ServiceRequestPublicUpdate, SubscriptionPlan


class TrialSignupForm(forms.Form):
    company_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Acme Managed Services",
                "autofocus": True,
            }
        ),
        label="Company name",
    )
    full_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Jane Doe",
            }
        ),
        label="Your full name",
    )
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "placeholder": "jane@acme.com",
            }
        ),
        label="Work email",
    )
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "jane.admin",
            }
        ),
        label="Username",
    )
    password1 = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Create a password",
            }
        ),
        label="Password",
    )
    password2 = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Confirm password",
            }
        ),
        label="Confirm password",
    )
    plan = forms.ModelChoiceField(
        queryset=SubscriptionPlan.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
        empty_label=None,
        label="Trial plan",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plan"].queryset = SubscriptionPlan.objects.filter(is_active=True).order_by("monthly_price_cents")

    def clean_company_name(self):
        company_name = self.cleaned_data["company_name"].strip()
        if Client.objects.filter(name__iexact=company_name).exists():
            raise forms.ValidationError("A client with this company name already exists.")
        return company_name

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is already taken.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "The passwords do not match.")
        return cleaned_data


class AgentInstallerUploadForm(forms.Form):
    installer = forms.FileField(
        widget=forms.ClearableFileInput(
            attrs={
                "class": "form-control",
                "accept": ".exe",
            }
        ),
        label="Windows agent installer (.exe)",
    )

    def clean_installer(self):
        installer = self.cleaned_data["installer"]
        filename = (installer.name or "").lower()
        if not filename.endswith(".exe"):
            raise forms.ValidationError("Upload a Windows executable (.exe) file.")

        max_size_bytes = 250 * 1024 * 1024
        if installer.size > max_size_bytes:
            raise forms.ValidationError("Installer file is too large. Maximum allowed size is 250 MB.")

        return installer


class ClientLoginForm(AuthenticationForm):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Username",
                "autofocus": True,
            }
        ),
    )
    password = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Password",
            }
        ),
    )


class ServiceRequestForm(forms.ModelForm):
    class Meta:
        model = ServiceRequest
        fields = ["machine", "subject", "priority", "description"]
        widgets = {
            "machine": forms.Select(attrs={"class": "form-select"}),
            "subject": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Printer issue, login problem, software install...",
                }
            ),
            "priority": forms.Select(attrs={"class": "form-select"}),
            "description": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 4,
                    "placeholder": "Tell us what is happening, when it started, and what you have already tried.",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        machine_queryset = kwargs.pop("machine_queryset", None)
        super().__init__(*args, **kwargs)
        self.fields["machine"].required = False
        self.fields["machine"].empty_label = "General request"
        if machine_queryset is not None:
            self.fields["machine"].queryset = machine_queryset


class TechnicianServiceRequestUpdateForm(forms.ModelForm):
    class Meta:
        model = ServiceRequest
        fields = ["status", "resolution_summary"]
        widgets = {
            "status": forms.Select(attrs={"class": "form-select"}),
            "resolution_summary": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 4,
                    "placeholder": "Add troubleshooting notes, the resolution, or the next step for the client.",
                }
            ),
        }


class TechnicianServiceRequestNoteForm(forms.ModelForm):
    class Meta:
        model = ServiceRequestNote
        fields = ["body"]
        widgets = {
            "body": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 4,
                    "placeholder": "Add an internal technician note. Clients will not see this.",
                }
            ),
        }


class TechnicianServiceRequestPublicUpdateForm(forms.ModelForm):
    class Meta:
        model = ServiceRequestPublicUpdate
        fields = ["body"]
        widgets = {
            "body": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 4,
                    "placeholder": "Share a progress update the client team can see in their portal.",
                }
            ),
        }


class ClientInvitationForm(forms.ModelForm):
    class Meta:
        model = ClientInvitation
        fields = ["email", "role"]
        widgets = {
            "email": forms.EmailInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "teammate@company.com",
                }
            ),
            "role": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].choices = [
            choice for choice in ClientAccess.ROLE_CHOICES if choice[0] != ClientAccess.ROLE_OWNER
        ]


class ClientInvitationAcceptForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Choose a username",
                "autofocus": True,
            }
        ),
    )
    first_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "First name",
            }
        ),
    )
    last_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Last name",
            }
        ),
    )
    password1 = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Create a password",
            }
        ),
    )
    password2 = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Confirm password",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        self.invitation = kwargs.pop("invitation")
        super().__init__(*args, **kwargs)

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is already in use.")
        return username

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "The passwords do not match.")
        if User.objects.filter(email__iexact=self.invitation.email).exists():
            raise forms.ValidationError(
                "An account with this email already exists. A technician can link that user manually if needed."
            )
        return cleaned_data


class ClientAccessUpdateForm(forms.ModelForm):
    class Meta:
        model = ClientAccess
        fields = ["role", "can_restart_machines"]
        widgets = {
            "role": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "can_restart_machines": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        allow_owner = kwargs.pop("allow_owner", False)
        super().__init__(*args, **kwargs)
        if allow_owner:
            self.fields["role"].choices = ClientAccess.ROLE_CHOICES
        else:
            self.fields["role"].choices = [
                choice for choice in ClientAccess.ROLE_CHOICES if choice[0] != ClientAccess.ROLE_OWNER
            ]


class ClientSettingsForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ["contact_email", "logo"]
        widgets = {
            "contact_email": forms.EmailInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "support@company.com",
                }
            ),
            "logo": forms.ClearableFileInput(
                attrs={
                    "class": "form-control",
                    "accept": "image/*",
                }
            ),
        }


class ClientBillingForm(forms.Form):
    plan = forms.ModelChoiceField(
        queryset=SubscriptionPlan.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
        empty_label=None,
        label="Select a plan",
    )
    billing_email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "placeholder": "billing@company.com",
            }
        ),
        label="Billing email",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plan"].queryset = SubscriptionPlan.objects.filter(is_active=True).order_by("monthly_price_cents")


class ExistingUserAccessForm(forms.Form):
    user = forms.ModelChoiceField(
        queryset=User.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
        empty_label="Select an existing user",
    )
    role = forms.ChoiceField(
        choices=[],
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    can_restart_machines = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, **kwargs):
        user_queryset = kwargs.pop("user_queryset", User.objects.none())
        allow_owner = kwargs.pop("allow_owner", False)
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = user_queryset.order_by("first_name", "username")
        if allow_owner:
            self.fields["role"].choices = ClientAccess.ROLE_CHOICES
        else:
            self.fields["role"].choices = [
                choice for choice in ClientAccess.ROLE_CHOICES if choice[0] != ClientAccess.ROLE_OWNER
            ]
