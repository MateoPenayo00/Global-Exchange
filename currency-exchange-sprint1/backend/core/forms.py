from django import forms


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
