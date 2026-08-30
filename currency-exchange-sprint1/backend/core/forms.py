from django import forms


class DeleteAccountForm(forms.Form):
    confirmation = forms.CharField(
        label="Type DELETE to confirm",
        max_length=20,
        widget=forms.TextInput(attrs={"autocomplete": "off", "placeholder": "DELETE"}),
        help_text="Type DELETE in full to confirm that you want to remove the current Keycloak account.",
    )

    def clean_confirmation(self):
        value = self.cleaned_data["confirmation"].strip().upper()
        if value != "DELETE":
            raise forms.ValidationError("Type DELETE exactly to confirm the account deletion.")
        return value
