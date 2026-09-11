from django import forms

from .roles import ALL_ROLES, DEFAULT_ROLE, ROLE_LABELS

_ROLE_CHOICES = [(role, ROLE_LABELS[role]) for role in ALL_ROLES]


class DeleteAccountForm(forms.Form):
    confirmation = forms.CharField(
        label="Escribe DELETE para confirmar",
        max_length=20,
        widget=forms.TextInput(attrs={"autocomplete": "off", "placeholder": "DELETE"}),
        help_text="Escribe DELETE completo para confirmar que quieres eliminar la cuenta actual de Keycloak.",
    )

    def clean_confirmation(self):
        value = self.cleaned_data["confirmation"].strip().upper()
        if value != "DELETE":
            raise forms.ValidationError("Escribe DELETE exactamente para confirmar la eliminación de la cuenta.")
        return value


class AdminUserForm(forms.Form):
    """Used by an admin to create a new Keycloak account from the management tab."""

    username = forms.CharField(label="Nombre de usuario", max_length=150)
    email = forms.EmailField(label="Correo")
    first_name = forms.CharField(label="Nombre", max_length=150, required=False)
    last_name = forms.CharField(label="Apellido", max_length=150, required=False)
    password = forms.CharField(label="Contraseña inicial", min_length=8, widget=forms.PasswordInput)
    temporary_password = forms.BooleanField(
        label="Pedir cambio de contraseña en el primer inicio de sesión",
        required=False,
        initial=True,
    )
    roles = forms.MultipleChoiceField(
        label="Roles",
        choices=_ROLE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        initial=[DEFAULT_ROLE],
    )

    def clean_username(self):
        return self.cleaned_data["username"].strip()

    def clean_roles(self):
        return set(self.cleaned_data["roles"])


class AdminUserRolesForm(forms.Form):
    """Edit the roles of an existing account."""

    roles = forms.MultipleChoiceField(
        label="Roles",
        choices=_ROLE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    def clean_roles(self):
        return set(self.cleaned_data["roles"])
