from django import forms

class MultipleImportForm(forms.Form):
    fichier_unite = forms.FileField(
        label="Fichier Unités",
        widget=forms.FileInput(attrs={'class': 'input-file-custom'})
    )
    fichier_collaborateur = forms.FileField(
        label="Fichier Collaborateurs",
        widget=forms.FileInput(attrs={'class': 'input-file-custom'})
    )